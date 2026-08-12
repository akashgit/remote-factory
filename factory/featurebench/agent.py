"""FeatureBench agent adapter — runs factory workflow inside FeatureBench containers."""

from featurebench.infer.agents.base import BaseAgent


class FactoryAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "factory"

    @property
    def install_script(self) -> str:
        return """
        set -euo pipefail

        # Install uv
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.cargo/bin:$PATH"

        # Install NVM + Node.js (needed for Claude Code CLI)
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        nvm install 22
        nvm use 22

        # Install Claude Code CLI
        npm install -g @anthropic-ai/claude-code

        # Clone and install factory
        git clone https://github.com/colehurwitz/remote-factory /opt/factory
        cd /opt/factory
        uv sync

        # Add to PATH
        echo 'export PATH="/opt/factory/.venv/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc
        echo 'export NVM_DIR="$HOME/.nvm"' >> ~/.bashrc
        echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' >> ~/.bashrc
        """

    def get_env_setup_script(self) -> str:
        script = super().get_env_setup_script()
        script += f"""
        export ANTHROPIC_API_KEY="{self.env_vars.get('ANTHROPIC_API_KEY', '')}"
        export FACTORY_RUNNER="{self.env_vars.get('FACTORY_RUNNER', 'claude')}"
        export PATH="/opt/factory/.venv/bin:$HOME/.cargo/bin:$PATH"
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        """
        return script

    def get_run_command(self, instruction: str) -> str:
        return f"""
        source ~/.bashrc
        cd /testbed

        # Write problem statement to file for the workflow to read
        cat > /testbed/problem_statement.md <<'FEATUREBENCH_PROBLEM_EOF'
{instruction}
FEATUREBENCH_PROBLEM_EOF

        # Initialize .factory directory for workflow artifacts
        mkdir -p /testbed/.factory/reviews
        mkdir -p /testbed/.factory/strategy
        mkdir -p /testbed/.factory/archive

        # Run factory featurebench workflow
        factory workflow run featurebench --project /testbed \
          2>&1 | tee /agent-logs/factory_output.log

        # Ensure all changes are committed (harness extracts via git diff)
        cd /testbed
        git add -A
        git diff --cached --quiet || git commit -m "FeatureBench: implement feature"
        """

    def pre_run_hook(self, container, log_file) -> bool:
        exit_code, _ = self.cm.exec_command(
            container,
            "mkdir -p /agent-logs",
            log_file=log_file,
        )
        return exit_code == 0

    def post_run_hook(self, container, log_file) -> bool:
        exit_code, _ = self.cm.exec_command(
            container,
            "cd /testbed && git log --oneline -1 | grep -q 'FeatureBench'",
            log_file=log_file,
        )

        if exit_code == 0:
            self.logger.info("Factory workflow completed with committed changes")
            return True

        # Fallback: check if there are any staged or unstaged changes
        exit_code, _ = self.cm.exec_command(
            container,
            "cd /testbed && ! git diff --quiet HEAD",
            log_file=log_file,
        )

        if exit_code == 0:
            self.logger.warning("Factory produced uncommitted changes — staging and committing")
            self.cm.exec_command(
                container,
                "cd /testbed && git add -A && git commit -m 'FeatureBench: implement feature (auto-commit)'",
                log_file=log_file,
            )
            return True

        self.logger.error("Factory workflow produced no changes")
        return False
