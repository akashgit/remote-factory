"""FeatureBench agent adapter — runs Claude Code directly inside FeatureBench containers."""

from featurebench.infer.agents.base import BaseAgent


class FactoryAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "factory"

    @property
    def install_script(self) -> str:
        return """
        set -euo pipefail

        # Install gcloud CLI (needed for Vertex AI auth)
        curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts --install-dir=/opt
        echo 'export PATH="/opt/google-cloud-sdk/bin:$PATH"' >> ~/.bashrc
        export PATH="/opt/google-cloud-sdk/bin:$PATH"

        # Install uv (may be needed by testbed projects)
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

        # Add to PATH
        echo 'export PATH="/opt/google-cloud-sdk/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc
        echo 'export NVM_DIR="$HOME/.nvm"' >> ~/.bashrc
        echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' >> ~/.bashrc
        """

    def get_env_setup_script(self) -> str:
        script = super().get_env_setup_script()

        # API key auth (direct)
        api_key = self.env_vars.get('ANTHROPIC_API_KEY', '')
        if api_key:
            script += f'export ANTHROPIC_API_KEY="{api_key}"\n'

        # Vertex AI auth
        vertex = self.env_vars.get('CLAUDE_CODE_USE_VERTEX', '')
        if vertex:
            script += f'export CLAUDE_CODE_USE_VERTEX="{vertex}"\n'
            script += f'export ANTHROPIC_VERTEX_PROJECT_ID="{self.env_vars.get("ANTHROPIC_VERTEX_PROJECT_ID", "")}"\n'
            script += f'export CLOUD_ML_REGION="{self.env_vars.get("CLOUD_ML_REGION", "us-east5")}"\n'

        # Write ADC credentials file if provided as env var
        adc_json = self.env_vars.get('GOOGLE_APPLICATION_CREDENTIALS_JSON', '')
        if adc_json:
            script += 'mkdir -p ~/.config/gcloud\n'
            script += 'echo $GOOGLE_APPLICATION_CREDENTIALS_JSON > ~/.config/gcloud/application_default_credentials.json\n'

        script += 'export PATH="/opt/google-cloud-sdk/bin:$HOME/.cargo/bin:$PATH"\n'
        script += 'export NVM_DIR="$HOME/.nvm"\n'
        script += '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"\n'
        return script

    def get_run_command(self, instruction: str) -> str:
        prompt = (
            "Read /testbed/problem_statement.md for the task. "
            "Implement the feature described in the problem statement. The codebase is at /testbed/. "
            "Do NOT create, modify, or recreate any test files (tests/*). Only modify source code. "
            "Read the Interface Descriptions carefully and match them exactly."
        )
        return f"""
        source ~/.bashrc
        cd /testbed

        # Write problem statement to file
        cat > /testbed/problem_statement.md <<'FEATUREBENCH_PROBLEM_EOF'
{instruction}
FEATUREBENCH_PROBLEM_EOF

        # Run Claude Code directly in headless mode
        claude -p '{prompt}' \
          --allowedTools 'Edit' 'Read' 'Write' 'Bash(command:*)' \
          --max-turns 30 \
          --output-format text \
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
