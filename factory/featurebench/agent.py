"""FeatureBench agent adapter — runs the real factory workflow pipeline.

Installs the full factory package inside the Docker container and invokes
`factory workflow run featurebench /testbed` — the 10-node pipeline:
  researcher → strategist → builder → code_reviewer → gate_review →
    adversarial_tester → gate_qa → health_checker → gate_tests → archivist

Each node spawns a separate `claude` subprocess via the workflow executor.
"""

import json
import shlex
from pathlib import Path

from featurebench.infer.agents.base import BaseAgent
from featurebench.infer.container import DOCKER_HOST_GATEWAY


class FactoryAgent(BaseAgent):
    # Path to the pre-built factory wheel (set before running)
    FACTORY_WHEEL: str | None = None

    @property
    def name(self) -> str:
        return "factory"

    @property
    def install_script(self) -> str:
        return """
        set -euo pipefail

        # Install NVM + Node.js (needed for Claude Code CLI)
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

        # Pre-configure Claude Code to allow all tools (needed since
        # --dangerously-skip-permissions is rejected when running as root)
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
        """Override to copy factory wheel into container before install script runs."""
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

        # Find the factory repo root via the 'factory' package location
        import factory as factory_pkg
        factory_root = Path(factory_pkg.__file__).resolve().parent.parent
        dist_dir = factory_root / "dist"
        if dist_dir.exists():
            wheels = sorted(dist_dir.glob("remote_factory-*.whl"), reverse=True)
            if wheels:
                return wheels[0]

        # Build one
        import subprocess
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
            "FORCE_AUTO_BACKGROUND_TASKS": "1",
            "ENABLE_BACKGROUND_TASKS": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

        # API key auth (direct)
        api_key = self.env_vars.get('ANTHROPIC_API_KEY', '')
        if api_key:
            required_vars["ANTHROPIC_API_KEY"] = api_key

        # Model routing — force all tiers to same model
        model = self._kwargs.get("model")
        if model:
            if "/" in model:
                model = model.split("/")[-1]
            required_vars["ANTHROPIC_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
            required_vars["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model

        # Vertex AI auth
        vertex = self.env_vars.get('CLAUDE_CODE_USE_VERTEX', '')
        if vertex:
            required_vars["CLAUDE_CODE_USE_VERTEX"] = vertex
            required_vars["ANTHROPIC_VERTEX_PROJECT_ID"] = self.env_vars.get(
                "ANTHROPIC_VERTEX_PROJECT_ID", ""
            )
            required_vars["CLOUD_ML_REGION"] = self.env_vars.get(
                "CLOUD_ML_REGION", "us-east5"
            )

        # Merge any additional env vars
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

        # Write ADC credentials file if JSON provided as env var
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

    def get_run_command(self, instruction: str) -> str:
        return (
            'export PATH="$HOME/.cargo/bin:$PATH"; '
            "NVM_DIR=${NVM_DIR:-$HOME/.nvm}; "
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" || true; '
            "cd /testbed && "
            "factory workflow run featurebench /testbed "
            "2>&1 | tee /agent-logs/factory_stream_output.jsonl"
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

        # Copy stream output
        stream_copied = self.cm.copy_from_container(
            container,
            "/agent-logs/factory_stream_output.jsonl",
            log_dir / "factory_stream_output.jsonl",
        )

        if not stream_copied:
            self.logger.error("Failed to copy factory_stream_output.jsonl from container")
            return False

        # Auto-commit any uncommitted changes
        self.cm.exec_command(
            container,
            "cd /testbed && git add -A && "
            "git diff --cached --quiet || "
            "git commit -m 'FeatureBench: implement feature'",
            log_file=log_file,
        )

        # Check if there's a meaningful commit
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
