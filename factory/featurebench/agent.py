"""FeatureBench agent adapter — hybrid host/container execution.

Orchestration agents (researcher, strategist, archivist) run on the HOST where
Claude Code is already installed. Execution agents (builder, health_checker) run
inside the FeatureBench container via podman exec, routed by the WorkflowExecutor
based on node metadata.

File sync between host and container uses podman cp:
  - Pre-workflow: extract problem_statement.md from container to host
  - Pre-container-node: sync .factory/strategy/ and .factory/reviews/ into container
  - Post-container-node: sync .factory/reviews/ back from container
  - Post-workflow: extract git diff from container for patch generation
"""

import subprocess
from pathlib import Path

from featurebench.infer.agents.base import BaseAgent


class FactoryAgent(BaseAgent):
    FACTORY_WHEEL: str | None = None

    @property
    def name(self) -> str:
        return "factory"

    @property
    def install_script(self) -> str:
        return """
        set -euo pipefail

        # Install the factory package from the wheel copied by install()
        WHEEL=$(ls /installed-agent/remote_factory-*.whl 2>/dev/null | head -1)
        if [ -n "$WHEEL" ]; then
            pip install "$WHEEL"
        else
            echo "ERROR: No remote_factory wheel found in /installed-agent/"
            ls -la /installed-agent/
            exit 1
        fi

        # Verify factory CLI is available
        factory --help >/dev/null 2>&1 || { echo "ERROR: factory CLI not available after install"; exit 1; }

        # Create .factory directory structure inside container
        mkdir -p /testbed/.factory/reviews /testbed/.factory/strategy /testbed/.factory/archive

        echo "Factory agent installation complete (minimal container install)"
        """

    def install(self, container, log_file) -> bool:
        """Copy factory wheel into container before install script runs."""
        wheel_path = self._resolve_wheel()
        self.logger.info(f"Copying factory wheel to container: {wheel_path}")
        dest = f"/installed-agent/{wheel_path.name}"
        self.cm.copy_to_container(container, wheel_path, dest)
        return super().install(container, log_file)

    def _resolve_wheel(self) -> Path:
        """Find or build the factory wheel."""
        if self.FACTORY_WHEEL:
            p = Path(self.FACTORY_WHEEL)
            if p.exists():
                return p

        import factory as factory_pkg
        factory_root = Path(factory_pkg.__file__).resolve().parent.parent
        dist_dir = factory_root / "dist"
        if dist_dir.exists():
            wheels = sorted(dist_dir.glob("remote_factory-*.whl"), reverse=True)
            if wheels:
                return wheels[0]

        self.logger.info("Building factory wheel...")
        subprocess.run(
            ["uv", "build", "--wheel"],
            cwd=str(factory_root),
            capture_output=True,
            check=True,
        )
        wheels = sorted(dist_dir.glob("remote_factory-*.whl"), reverse=True)
        if wheels:
            return wheels[0]
        raise FileNotFoundError("Could not build factory wheel")

    def get_env_setup_script(self) -> str:
        lines = ["#!/bin/bash", ""]

        required_vars = {
            "DISABLE_TELEMETRY": "1",
        }

        api_key = self.env_vars.get('ANTHROPIC_API_KEY', '')
        if api_key:
            required_vars["ANTHROPIC_API_KEY"] = api_key

        model = self._kwargs.get("model")
        if model:
            if "/" in model:
                model = model.split("/")[-1]
            required_vars["ANTHROPIC_MODEL"] = model

        vertex = self.env_vars.get('CLAUDE_CODE_USE_VERTEX', '')
        if vertex:
            required_vars["CLAUDE_CODE_USE_VERTEX"] = vertex
            required_vars["ANTHROPIC_VERTEX_PROJECT_ID"] = self.env_vars.get(
                "ANTHROPIC_VERTEX_PROJECT_ID", ""
            )
            required_vars["CLOUD_ML_REGION"] = self.env_vars.get(
                "CLOUD_ML_REGION", "us-east5"
            )

        for key, value in self.env_vars.items():
            if key not in required_vars and value:
                required_vars[key] = value

        for key, value in required_vars.items():
            if value:
                escaped_value = str(value).replace("'", "'\\''")
                lines.append(f"export {key}='{escaped_value}'")

        lines.extend(self._get_proxy_unset_lines())

        if self.env_vars.get("GOOGLE_APPLICATION_CREDENTIALS_JSON"):
            lines.extend([
                "",
                "# Write ADC credentials for Vertex AI",
                "mkdir -p ~/.config/gcloud",
                'echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > '
                '~/.config/gcloud/application_default_credentials.json',
                'export GOOGLE_APPLICATION_CREDENTIALS='
                '"$HOME/.config/gcloud/application_default_credentials.json"',
            ])

        return "\n".join(lines)

    def get_run_command(self, instruction: str) -> str:
        return (
            "echo 'host-side workflow execution — container standing by for podman exec'; "
            "sleep infinity"
        )

    def prepare_run(self, container, instruction: str, log_file) -> bool:
        """Write problem_statement.md into the container before running the workflow."""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as tmp:
            tmp.write(instruction)
            tmp_path = Path(tmp.name)

        try:
            self.cm.copy_to_container(container, tmp_path, "/testbed/problem_statement.md")
        finally:
            tmp_path.unlink(missing_ok=True)

        exit_code, output = self.cm.exec_command(
            container, "test -s /testbed/problem_statement.md", log_file=log_file
        )
        if exit_code != 0:
            self.logger.error("Failed to write problem_statement.md")
            return False
        self.logger.info("Wrote problem_statement.md to /testbed/")
        return True

    def _extract_problem_statement(self, container, host_project_path: Path, log_file) -> bool:
        """Copy problem_statement.md from container to host project path."""
        success = self.cm.copy_from_container(
            container,
            "/testbed/problem_statement.md",
            host_project_path / "problem_statement.md",
        )
        if not success:
            self.logger.error("Failed to extract problem_statement.md from container")
        return success

    def _sync_to_container(self, container, host_project_path: Path, log_file) -> bool:
        """Sync .factory/strategy/ and .factory/reviews/ from host into container."""
        success = True
        for subdir in ("strategy", "reviews"):
            src = host_project_path / ".factory" / subdir
            if src.exists():
                for f in src.iterdir():
                    if f.is_file():
                        dest = f"/testbed/.factory/{subdir}/{f.name}"
                        if not self.cm.copy_to_container(container, f, dest):
                            self.logger.warning(f"Failed to sync {f.name} to container")
                            success = False
        return success

    def _sync_from_container(self, container, host_project_path: Path, log_file) -> bool:
        """Sync .factory/reviews/ from container back to host."""
        reviews_dir = host_project_path / ".factory" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)

        exit_code, output = self.cm.exec_command(
            container,
            "ls /testbed/.factory/reviews/ 2>/dev/null",
            log_file=log_file,
        )
        if exit_code != 0 or not output:
            return True

        success = True
        for filename in output.strip().split("\n"):
            filename = filename.strip()
            if filename:
                if not self.cm.copy_from_container(
                    container,
                    f"/testbed/.factory/reviews/{filename}",
                    reviews_dir / filename,
                ):
                    self.logger.warning(f"Failed to sync {filename} from container")
                    success = False
        return success

    def pre_run_hook(self, container, log_file) -> bool:
        exit_code, _ = self.cm.exec_command(
            container,
            "mkdir -p /agent-logs /testbed/.factory/reviews "
            "/testbed/.factory/strategy /testbed/.factory/archive",
            log_file=log_file,
        )
        return exit_code == 0

    def post_run_hook(self, container, log_file) -> bool:
        self.cm.exec_command(
            container,
            "cd /testbed && git add -A && "
            "git diff --cached --quiet || "
            "git commit -m 'FeatureBench: implement feature'",
            log_file=log_file,
        )

        exit_code, output = self.cm.exec_command(
            container,
            "cd /testbed && git diff --stat HEAD~1 HEAD 2>/dev/null "
            "| grep -v problem_statement.md | grep -v '^ [0-9]' | head -5",
            log_file=log_file,
        )

        if output and output.strip():
            self.logger.info("Factory workflow completed with code changes")
            return True

        exit_code, _ = self.cm.exec_command(
            container,
            "cd /testbed && git log --oneline -1 | grep -q 'FeatureBench'",
            log_file=log_file,
        )
        if exit_code == 0:
            self.logger.warning("Factory committed but may not have code changes")
            return True

        self.logger.error("Factory workflow produced no changes")
        return False

    def failure_hook(self, container, log_file) -> None:
        log_dir = Path(log_file).parent
        try:
            self.cm.copy_from_container(
                container,
                "/agent-logs/factory_stream_output.jsonl",
                log_dir / "factory_stream_output.jsonl",
            )
        except Exception:
            pass
