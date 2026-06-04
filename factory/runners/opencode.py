"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from factory.runners._subprocess import run_subprocess

if TYPE_CHECKING:
    from factory.models import AgentRunRequest, AgentRunResult
    from factory.runners.protocol import RunnerMeta

log = structlog.get_logger()

_auth_checked = False


class OpenCodeAuthError(Exception):
    """Raised when OPENAI_API_KEY is not set."""

    def __init__(self) -> None:
        super().__init__(
            "OPENAI_API_KEY environment variable is not set. "
            "Set it directly or add it to a config.toml credential profile: "
            "[credentials.opencode] OPENAI_API_KEY = \"...\""
        )


def _check_auth() -> None:
    """Check that OPENAI_API_KEY is set (once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("OPENAI_API_KEY"):
        _auth_checked = True
        return
    raise OpenCodeAuthError()


def is_opencode_dry_run() -> bool:
    """Return True if OpenCode dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("opencode_dry_run", env_var="FACTORY_OPENCODE_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class OpenCodeRunner:
    """Runner implementation for OpenCode CLI."""

    name: str = "opencode"

    @classmethod
    def metadata(cls) -> RunnerMeta:
        from factory.runners.protocol import RunnerMeta
        return RunnerMeta(
            name="opencode",
            display_name="OpenCode",
            binary="opencode",
            install_hint="go install github.com/opencode-ai/opencode@latest",
            required_env_vars=["OPENAI_API_KEY"],
            supports_model_override=False,
            supports_interactive=True,
            supports_streaming=True,
            supports_usage_telemetry=False,
            supports_session_name=False,
        )

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless OpenCode invocation."""
        from factory.models import AgentRunResult

        if is_opencode_dry_run():
            stdout, code = self._dry_run_response(request.role, request.cwd, request.task)
            return AgentRunResult(stdout=stdout, return_code=code)

        _check_auth()

        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = [
            "opencode",
            "-p", full_prompt,
            "-c", str(request.cwd),
            "-f", "json",
            "-q",
        ]

        log.info("opencode_headless", cwd=str(request.cwd), role=request.role)

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

        result = await run_subprocess(
            cmd, cwd=str(request.cwd), env=env,
            timeout=request.timeout, runner_name="opencode", role=request.role,
        )

        result_text = result.stdout
        try:
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                content = data.get("content", result.stdout)
                result_text = content if isinstance(content, str) else result.stdout
        except (json.JSONDecodeError, ValueError):
            log.debug("opencode_json_parse_failed")

        return AgentRunResult(
            stdout=result_text,
            return_code=result.return_code,
            metadata=result.metadata,
        )

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive OpenCode session as a subprocess."""
        if is_opencode_dry_run():
            print("[DRY-RUN] Would exec: opencode (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        cmd = ["opencode", "-c", str(request.cwd)]

        log.info("opencode_interactive", cwd=str(request.cwd))

        result = subprocess.run(cmd, cwd=request.cwd)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        response = (
            f"[DRY-RUN] OpenCodeRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        log.info("opencode_dry_run", role=role, cwd=str(cwd))
        return response, 0
