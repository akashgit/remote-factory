"""GlaudeRunner — Glaude (GLM-5.2 LiteLLM proxy) CLI backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess
from factory.runners.claude import _make_ceo_message_emitter, _parse_usage

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()


def is_glaude_dry_run() -> bool:
    """Return True if Glaude dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("glaude_dry_run", env_var="FACTORY_GLAUDE_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class GlaudeRunner:
    """Runner implementation for Glaude CLI (Claude Code via GLM-5.2 LiteLLM proxy)."""

    name: str = "glaude"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="glaude",
            display_name="Glaude (GLM-5.2)",
            binary="glaude",
            install_hint="Install glaude wrapper from your team's internal tooling",
            required_env_vars=[],
            supports_model_override=True,
            supports_usage_telemetry=True,
            supports_session_name=True,
            supports_background=True,
        )

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the Glaude CLI command, env dict, and temp files."""
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        prompt_file.write(request.prompt)
        prompt_file.close()
        prompt_path = Path(prompt_file.name)

        cmd = [
            "glaude", "--append-system-prompt-file", prompt_file.name,
            "-p", request.task,
            "--output-format", "stream-json",
            "--verbose",
            "--disallowedTools", "Agent",
        ]
        settings_file = request.extras.get("settings_file")
        if settings_file:
            cmd.extend(["--settings", str(settings_file)])
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if request.model:
            env["FACTORY_MODEL"] = request.model

        return cmd, env, [prompt_path]

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless Glaude invocation."""
        from factory.models import AgentRunResult

        if is_glaude_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            return make_dry_run_result("glaude", request.role, request.cwd, request.task)

        background = request.extras.get("background", False)
        if background:
            from factory.runners._background import run_in_background

            stdout, rc, usage = await run_in_background(
                request.prompt, request.task, request.cwd, request.role,
                timeout=request.timeout,
                model=request.model,
                dangerously_skip_permissions=request.skip_permissions,
            )
            return AgentRunResult(stdout=stdout, return_code=rc, usage=usage)

        tmux_persist = request.extras.get("tmux_persist", False)
        if tmux_persist:
            from factory.runners._tmux_persist import find_project_path, run_in_tmux, tmux_available

            if tmux_available():
                stdout, rc, usage = await run_in_tmux(
                    request.prompt, request.task, request.cwd, request.role,
                    find_project_path(request.cwd),
                    model=request.model,
                    dangerously_skip_permissions=request.skip_permissions,
                )
                return AgentRunResult(stdout=stdout, return_code=rc, usage=usage)
            log.warning("tmux_not_available")

        cmd, env, temp_files = self.build_command(request)
        env["TELEMETRY_PLATFORM"] = ""
        try:
            log.info("glaude_headless", cwd=str(request.cwd), model=request.model)

            on_line = None
            if request.role == "ceo" and request.project_path is not None:
                on_line = _make_ceo_message_emitter(request.project_path)

            result = await run_subprocess(
                cmd, cwd=str(request.cwd), env=env,
                timeout=request.timeout, runner_name="glaude", role=request.role,
                on_line=on_line,
            )

            usage = None
            result_text = result.stdout
            metadata: dict[str, object] = {**result.metadata}

            data: dict[str, object] | None = None
            for line in reversed(result.stdout.strip().splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict) and "result" in parsed:
                    data = parsed
                    break

            if data is not None:
                result_value = data.get("result", result.stdout)
                result_text = result_value if isinstance(result_value, str) else result.stdout
                usage = _parse_usage(data)
                for key in ("session_id", "uuid", "stop_reason", "terminal_reason",
                            "duration_api_ms", "ttft_ms", "is_error", "subtype"):
                    metadata[key] = data.get(key)
                metadata["model_usage"] = data.get("modelUsage")
                metadata["permission_denials"] = data.get("permission_denials")

            return AgentRunResult(
                stdout=result_text,
                return_code=result.return_code,
                usage=usage,
                metadata=metadata,
            )
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def build_interactive_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the CLI command, env dict, and temp files for an interactive invocation."""
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="factory-prompt-", delete=False,
        )
        prompt_file.write(request.prompt)
        prompt_file.close()
        prompt_path = Path(prompt_file.name)

        temp_files: list[Path] = [prompt_path]

        cwd = Path(request.cwd)
        claude_dir = cwd / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        claude_md_path = claude_dir / "CLAUDE.md"
        claude_md_path.write_text(request.prompt)
        temp_files.append(claude_md_path)

        settings_path = claude_dir / "settings.local.json"
        settings: dict[str, object] = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except (json.JSONDecodeError, ValueError):
                settings = {}
        settings["disallowedTools"] = ["Agent"]
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        temp_files.append(settings_path)

        cmd = [
            "glaude",
            "--append-system-prompt-file", prompt_file.name,
        ]
        settings_file = request.extras.get("settings_file")
        if settings_file:
            cmd.extend(["--settings", str(settings_file)])
        if request.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.append(request.task)
        if request.model:
            cmd.extend(["--model", request.model])
        if request.session_name:
            cmd.extend(["--name", request.session_name])

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        if request.model:
            env["FACTORY_MODEL"] = request.model

        return cmd, env, temp_files

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive Glaude session as a subprocess."""
        if is_glaude_dry_run():
            print("[DRY-RUN] Would exec: glaude (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        cmd, env, temp_files = self.build_interactive_command(request)
        if not env.get("FACTORY_TRACE_ID"):
            env["TELEMETRY_PLATFORM"] = ""
        try:
            log.info("glaude_interactive", cwd=str(request.cwd))
            result = subprocess.run(cmd, cwd=request.cwd, env=env)
            return result.returncode
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)
