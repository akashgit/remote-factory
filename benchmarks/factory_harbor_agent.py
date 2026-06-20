"""Harbor agent that runs ``factory ceo`` as a benchmark solver."""

import os
import re
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class FactoryCeo(BaseInstalledAgent):
    """Runs ``factory ceo`` to solve benchmark tasks.

    Installs Claude Code + the factory CLI inside the container, then
    invokes ``factory ceo . --headless --prompt <instruction>``.
    """

    @staticmethod
    @override
    def name() -> str:
        return "factory-ceo"

    @override
    def get_version_command(self) -> str | None:
        return (
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
            'factory --version 2>/dev/null || echo "unknown"'
        )

    @override
    def parse_version(self, stdout: str) -> str:
        match = re.search(r"(\d+\.\d+\.\d+)", stdout.strip())
        return match.group(1) if match else stdout.strip()

    # ── install ──────────────────────────────────────────────────────

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # System packages (as root)
        await self.exec_as_root(
            environment,
            command=(
                "if command -v apk &> /dev/null; then"
                "  apk add --no-cache curl bash nodejs npm procps git;"
                " elif command -v apt-get &> /dev/null; then"
                "  apt-get update && apt-get install -y curl procps git;"
                " elif command -v yum &> /dev/null; then"
                "  yum install -y curl procps-ng git;"
                " fi"
            ),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        # Claude Code — the underlying runner that factory spawns
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if command -v apk &> /dev/null; then"
                "  npm install -g @anthropic-ai/claude-code;"
                " else"
                "  curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh"
                " | bash -s --;"
                " fi && "
                "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc && "
                'export PATH="$HOME/.local/bin:$PATH" && '
                "claude --version"
            ),
        )

        # Factory CLI via uv
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                'export PATH="$HOME/.local/bin:$PATH"; '
                "curl -LsSf https://astral.sh/uv/install.sh | sh && "
                'export PATH="$HOME/.cargo/bin:$PATH"; '
                "uv tool install "
                "'remote-factory @ git+https://github.com/akashgit/remote-factory.git' && "
                "factory --version"
            ),
        )

    # ── run ───────────────────────────────────────────────────────────

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Run factory ceo to solve the task described in *instruction*."""
        api_key = (
            self._get_env("ANTHROPIC_API_KEY")
            or self._get_env("ANTHROPIC_AUTH_TOKEN")
            or ""
        )

        env: dict[str, str] = {
            "ANTHROPIC_API_KEY": api_key,
            "IS_SANDBOX": "1",
            "CLAUDE_CONFIG_DIR": "/logs/agent/sessions",
        }

        if self.model_name:
            env["ANTHROPIC_MODEL"] = self.model_name.split("/")[-1]

        for var in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "CLAUDE_CODE_USE_VERTEX",
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "CLOUD_ML_REGION",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
            "MAX_THINKING_TOKENS",
            "CLAUDE_CODE_EFFORT_LEVEL",
        ):
            val = self._get_env(var) or os.environ.get(var)
            if val and var not in env:
                env[var] = val

        env = {k: v for k, v in env.items() if v}

        # Create Claude Code config directories
        await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p $CLAUDE_CONFIG_DIR/debug "
                "$CLAUDE_CONFIG_DIR/projects "
                "$CLAUDE_CONFIG_DIR/shell-snapshots "
                "$CLAUDE_CONFIG_DIR/statsig "
                "$CLAUDE_CONFIG_DIR/todos "
                "$CLAUDE_CONFIG_DIR/skills"
            ),
            env=env,
        )

        # Minimal factory.md so factory recognizes the working directory
        await self.exec_as_agent(
            environment,
            command=(
                "cat > ./factory.md << 'FACTORYEOF'\n"
                "---\n"
                "goal: Solve the given coding task\n"
                "---\n"
                "FACTORYEOF"
            ),
            env=env,
        )

        # Write task instruction to a file for --prompt
        await self.exec_as_agent(
            environment,
            command=f"cat > /tmp/task-instruction.md << 'INSTREOF'\n{instruction}\nINSTREOF",
            env=env,
        )

        # Run factory ceo in headless mode
        await self.exec_as_agent(
            environment,
            command=(
                'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
                "factory ceo . --headless "
                "--prompt /tmp/task-instruction.md "
                "2>&1 </dev/null | tee /logs/agent/factory-ceo.txt"
            ),
            env=env,
        )
