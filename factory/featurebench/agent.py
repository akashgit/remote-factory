"""FeatureBench agent adapter — hybrid host/container workflow execution.

Runs the factory workflow pipeline with host-side orchestration:
  researcher (host) → strategist (host) → builder (container) →
    health_checker (container) → gate_tests (host) → archivist (host)

Host nodes (researcher, strategist, archivist) run on the host machine where
Claude Code is already installed. Container nodes (builder, health_checker)
are routed into the FeatureBench Docker container via `docker exec` by the
WorkflowExecutor's container routing (execution_context metadata).

File sync between host workspace and container is handled via `docker cp`
in pre/post node hooks.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from featurebench.infer.agents.base import BaseAgent
from featurebench.infer.container import DOCKER_HOST_GATEWAY


class FactoryAgent(BaseAgent):
    FACTORY_WHEEL: str | None = None

    @property
    def name(self) -> str:
        return "factory"

    @property
    def install_script(self) -> str:
        return """
        set -euo pipefail

        # Install NVM + Node.js (needed for Claude Code CLI inside container)
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        nvm install 22
        nvm use 22

        # Install Claude Code CLI
        npm install -g @anthropic-ai/claude-code

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

        # Pre-configure Claude Code to allow all tools
        mkdir -p ~/.claude
        cat > ~/.claude/settings.json << 'SETTINGS'
        {
          "permissions": {
            "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)", "WebFetch(*)", "WebSearch(*)"],
            "deny": []
          }
        }
SETTINGS
        cat > ~/.claude.json << 'STATE'
        {
          "hasCompletedOnboarding": true,
          "hasTrustDialogAccepted": true,
          "bypassPermissionsModeAccepted": true,
          "projects": {
            "/testbed": {
              "hasTrustDialogAccepted": true
            }
          }
        }
STATE

        echo "Factory agent installation complete"
        """

    def install(self, container, log_file) -> bool:
        """Copy factory wheel into container before install script runs."""
        wheel_path = self._resolve_wheel()
        self.logger.info(f"Copying factory wheel to container: {wheel_path}")
        dest = f"/installed-agent/{wheel_path.name}"
        self.cm.copy_to_container(container, wheel_path, dest)
        return super().install(container, log_file)

    def _resolve_wheel(self) -> Path:
        if self.FACTORY_WHEEL:
            p = Path(self.FACTORY_WHEEL)
            if p.exists():
                return p

        import factory as factory_pkg

        # Try 1: look relative to the factory source (works in editable installs
        # or when running from the repo directory)
        factory_root = Path(factory_pkg.__file__).resolve().parent.parent
        dist_dir = factory_root / "dist"
        if dist_dir.exists():
            wheels = sorted(dist_dir.glob("remote_factory-*.whl"), reverse=True)
            if wheels:
                return wheels[0]

        # Try 2: check dist-info direct_url.json for the original wheel path
        # (works when installed via `uv pip install <wheel>`)
        import importlib.metadata
        try:
            dist = importlib.metadata.distribution("remote-factory")
            for f in dist.files or []:
                if f.name == "direct_url.json":
                    import json
                    url_data = json.loads(f.read_text())
                    url = url_data.get("url", "")
                    if url.startswith("file://") and url.endswith(".whl"):
                        wheel_path = Path(url.removeprefix("file://"))
                        if wheel_path.exists():
                            return wheel_path
                        # Try the dist/ directory containing the referenced wheel
                        parent_dist = wheel_path.parent
                        if parent_dist.exists():
                            wheels = sorted(
                                parent_dist.glob("remote_factory-*.whl"),
                                reverse=True,
                            )
                            if wheels:
                                return wheels[0]
        except importlib.metadata.PackageNotFoundError:
            pass

        # Try 3: build from source
        import subprocess as sp
        self.logger.info("Building factory wheel...")
        sp.run(
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
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

        api_key = self.env_vars.get('ANTHROPIC_API_KEY', '')
        if api_key:
            required_vars["ANTHROPIC_API_KEY"] = api_key

        model = self._kwargs.get("model")
        if model:
            if "/" in model:
                model = model.split("/")[-1]
            required_vars["ANTHROPIC_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model

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
                value_str = str(value)
                if 'localhost' in value_str or '127.0.0.1' in value_str:
                    value_str = value_str.replace('localhost', DOCKER_HOST_GATEWAY)
                    value_str = value_str.replace('127.0.0.1', DOCKER_HOST_GATEWAY)
                escaped_value = value_str.replace("'", "'\\''")
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

        lines.extend([
            "",
            "# Load NVM + tools",
            'export PATH="$HOME/.cargo/bin:$PATH"',
            'export NVM_DIR="$HOME/.nvm"',
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
        ])

        return "\n".join(lines)

    def prepare_run(self, container, instruction: str, log_file) -> bool:
        """Write problem_statement.md into the container before running the workflow."""
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

    def get_run_command(self, instruction: str) -> str:
        # Not used — run() is overridden for host-side orchestration.
        # Kept as fallback for any code that calls it directly.
        return "echo 'ERROR: get_run_command should not be called in hybrid mode'; exit 1"

    def run(self, container, instruction: str, log_file, timeout=None) -> bool:
        """Run the workflow with host-side orchestration and container-side execution.

        Host nodes (researcher, strategist, archivist) run on the host.
        Container nodes (builder, health_checker) are routed into the
        FeatureBench Docker container via docker exec.
        """
        self.pre_run_hook(container, log_file)
        self.prepare_run(container, instruction, log_file)

        container_id = container.id
        container_name = container.name or container_id

        host_workspace = Path(tempfile.mkdtemp(prefix="fb-factory-"))

        try:
            self.logger.info(f"Copying testbed to host workspace: {host_workspace}")
            subprocess.run(
                ["docker", "cp", f"{container_id}:/testbed/.", str(host_workspace)],
                check=True,
                timeout=120,
            )

            for subdir in ("reviews", "strategy", "archive"):
                (host_workspace / ".factory" / subdir).mkdir(parents=True, exist_ok=True)

            success = self._run_workflow_on_host(
                host_workspace, container_id, container_name, log_file,
            )

            subprocess.run(
                ["docker", "exec", container_id, "bash", "-c",
                 "cd /testbed && git add -A && "
                 "git diff --cached --quiet || "
                 "git commit -m 'FeatureBench: implement feature'"],
                check=False,
                timeout=30,
            )

            success_post = self.post_run_hook(container, log_file)
            return success and success_post

        except Exception:
            self.logger.exception("Host-side workflow execution failed")
            self.failure_hook(container, log_file)
            return False
        finally:
            shutil.rmtree(host_workspace, ignore_errors=True)

    def _run_workflow_on_host(
        self, host_workspace: Path, container_id: str, container_name: str, log_file,
    ) -> bool:
        """Execute the workflow graph on the host with container routing."""
        import asyncio

        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.registry import WorkflowRegistry

        wf = WorkflowRegistry.get_workflow("featurebench", project_path=host_workspace)
        if wf is None:
            self.logger.error("Could not load featurebench workflow")
            return False

        async def pre_node_hook(node_id, node):
            if getattr(node, 'metadata', {}).get('execution_context') == 'container':
                self._sync_to_container(host_workspace, container_id)

        async def post_node_hook(node_id, node):
            if getattr(node, 'metadata', {}).get('execution_context') == 'container':
                self._sync_from_container(host_workspace, container_id)

        executor = WorkflowExecutor(
            workflow=wf,
            project_path=host_workspace,
            context={
                "container_name": container_name,
                "container_runtime": "docker",
                "container_env_script": "/installed-agent/setup-env.sh",
                "container_conda_env": "testbed",
            },
            pre_node_hook=pre_node_hook,
            post_node_hook=post_node_hook,
        )

        result = asyncio.run(executor.execute())

        self.logger.info(
            f"Workflow completed: success={result.success}, "
            f"nodes={result.nodes_executed}, duration={result.duration_ms:.0f}ms"
        )
        if result.halted:
            self.logger.warning(f"Workflow halted: {result.halt_reason}")

        return result.success

    def _sync_to_container(self, host_workspace: Path, container_id: str) -> None:
        """Sync .factory/ from host workspace to container /testbed/.factory/."""
        factory_dir = host_workspace / ".factory"
        if not factory_dir.exists():
            return
        subprocess.run(
            ["docker", "exec", container_id, "mkdir", "-p", "/testbed/.factory"],
            check=False, timeout=10,
        )
        subprocess.run(
            ["docker", "cp",
             f"{factory_dir}/.", f"{container_id}:/testbed/.factory/"],
            check=False, timeout=30,
        )

    def _sync_from_container(self, host_workspace: Path, container_id: str) -> None:
        """Sync .factory/ from container /testbed/.factory/ to host workspace."""
        factory_dir = host_workspace / ".factory"
        factory_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["docker", "cp",
             f"{container_id}:/testbed/.factory/.", str(factory_dir)],
            check=False, timeout=30,
        )

    def pre_run_hook(self, container, log_file) -> bool:
        exit_code, _ = self.cm.exec_command(
            container,
            "mkdir -p /agent-logs",
            log_file=log_file,
        )
        return exit_code == 0

    def post_run_hook(self, container, log_file) -> bool:
        log_dir = Path(log_file).parent

        stream_copied = self.cm.copy_from_container(
            container,
            "/agent-logs/factory_stream_output.jsonl",
            log_dir / "factory_stream_output.jsonl",
        )

        if not stream_copied:
            self.logger.warning(
                "No factory_stream_output.jsonl (expected with host-side orchestration)"
            )

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
