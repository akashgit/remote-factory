"""OpenCodeRunner — OpenCode CLI backend implementation."""

from __future__ import annotations

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


def _can_source_key_from_shell() -> bool:
    """Check if OPENAI_API_KEY can be sourced from ~/.zshrc."""
    try:
        result = subprocess.run(
            ["zsh", "-c", "source ~/.zshrc 2>/dev/null && echo $OPENAI_API_KEY"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_auth() -> None:
    """Check that OPENAI_API_KEY is available (env or shell profile, once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("OPENAI_API_KEY"):
        _auth_checked = True
        return
    if _can_source_key_from_shell():
        _auth_checked = True
        return
    raise OpenCodeAuthError()


def _find_opencode_bin_dir() -> str | None:
    """Find the directory containing the opencode binary."""
    import shutil

    oc_path = shutil.which("opencode")
    if oc_path:
        return str(Path(oc_path).parent)
    candidates = [
        Path.home() / "go" / "bin",
        Path(os.environ.get("GOPATH", "")) / "bin" if os.environ.get("GOPATH") else None,
    ]
    for d in candidates:
        if d is not None and (d / "opencode").is_file():
            return str(d)
    return None


def _prepend_opencode_path(env: dict[str, str]) -> None:
    """Prepend the opencode binary directory to PATH if found."""
    bin_dir = _find_opencode_bin_dir()
    if bin_dir:
        current_path = env.get("PATH", "")
        if not current_path.startswith(bin_dir):
            env["PATH"] = f"{bin_dir}:{current_path}"
            log.debug("opencode_path_prepended", dir=bin_dir)


def _source_openai_key_from_shell(env: dict[str, str]) -> None:
    """If OPENAI_API_KEY is missing, try sourcing it from ~/.zshrc into env (not os.environ)."""
    if env.get("OPENAI_API_KEY"):
        return
    try:
        result = subprocess.run(
            ["zsh", "-c", "source ~/.zshrc 2>/dev/null && echo $OPENAI_API_KEY"],
            capture_output=True, text=True, timeout=5,
        )
        key = result.stdout.strip()
        if key:
            env["OPENAI_API_KEY"] = key
            log.debug("openai_key_sourced_from_zshrc")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


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

    def build_command(self, request: AgentRunRequest) -> tuple[list[str], dict[str, str], list[Path]]:
        """Build the OpenCode CLI command and env dict."""
        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = [
            "opencode",
            "-p", full_prompt,
            "-c", str(request.cwd),
            "-q",
        ]

        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        _prepend_opencode_path(env)
        _source_openai_key_from_shell(env)

        return cmd, env, []

    async def headless(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a headless OpenCode invocation."""
        if is_opencode_dry_run():
            from factory.runners._subprocess import make_dry_run_result
            return make_dry_run_result("opencode", request.role, request.cwd, request.task)

        _check_auth()

        cmd, env, _ = self.build_command(request)

        log.info("opencode_headless", cwd=str(request.cwd), role=request.role)

        return await run_subprocess(
            cmd, cwd=str(request.cwd), env=env,
            timeout=request.timeout, runner_name="opencode", role=request.role,
        )

    def interactive_run(self, request: AgentRunRequest) -> int:
        """Run an interactive OpenCode session as a subprocess."""
        if is_opencode_dry_run():
            print("[DRY-RUN] Would exec: opencode (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        full_prompt = f"{request.prompt}\n\n---\n\n## Current Task\n\n{request.task}"
        cmd = ["opencode", "-p", full_prompt, "-c", str(request.cwd)]

        log.info("opencode_interactive", cwd=str(request.cwd))

        env = dict(os.environ)
        _prepend_opencode_path(env)
        _source_openai_key_from_shell(env)

        result = subprocess.run(cmd, cwd=request.cwd, env=env)
        return result.returncode

