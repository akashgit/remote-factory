"""CodexRunner — OpenAI Codex CLI backend implementation."""

from __future__ import annotations

import logging
import os

from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)

logger = logging.getLogger(__name__)

_auth_checked = False

_IDENTITY = RunnerIdentity(
    name="codex",
    display_name="OpenAI Codex",
    binary="codex",
    capabilities={Capability.MODEL_OVERRIDE, Capability.SANDBOXING},
)


class CodexAuthError(Exception):
    """Raised when neither CODEX_API_KEY nor OPENAI_API_KEY is set."""

    def __init__(self) -> None:
        super().__init__(
            "CODEX_API_KEY (or OPENAI_API_KEY) environment variable is not set. "
            "Set it directly or add it to a config.toml credential profile: "
            "[credentials.codex] CODEX_API_KEY = \"...\""
        )


def _check_auth() -> None:
    """Check that CODEX_API_KEY or OPENAI_API_KEY is set (once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        _auth_checked = True
        return
    raise CodexAuthError()


def is_codex_dry_run() -> bool:
    """Return True if Codex dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("codex_dry_run", env_var="FACTORY_CODEX_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


class CodexRunner(AgentRunner):
    """Runner implementation for OpenAI Codex CLI."""

    name: str = "codex"

    @property
    def identity(self) -> RunnerIdentity:
        return _IDENTITY

    def _build_command(
        self, request: Request, *, prompt_file: str | None = None
    ) -> list[str]:
        cmd = ["codex", "exec", request.prompt]
        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])
        if request.model:
            cmd.extend(["--model", request.model])
        return cmd

    def _build_env(self, request: Request) -> dict[str, str]:
        env = super()._build_env(request)
        if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
            env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
        return env

    def _parse_response(
        self, stdout: str, stderr: str, exit_code: int
    ) -> Response:
        return Response(output=stdout, exit_code=exit_code)

    async def check_health(self) -> tuple[bool, str]:
        """Check codex binary and API key."""
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            return True, f"{self.identity.display_name} found and API key set"
        return False, "CODEX_API_KEY (or OPENAI_API_KEY) not set"

    async def run(self, request: Request) -> Response:
        """Override for dry-run detection."""
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(request.role, request.cwd, request.task)
            return Response(output=stdout, exit_code=code)

        _check_auth()
        return await super().run(request)

    def run_interactive(self, request: Request) -> int:
        """Override for interactive Codex session."""
        import subprocess

        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {request.task[:200]}...")
            return 0

        _check_auth()

        cmd = ["codex", request.prompt]
        if request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])
        if request.model:
            cmd.extend(["--model", request.model])

        logger.info("CodexRunner interactive: cwd=%s", request.cwd)

        env = self._build_env(request)
        result = subprocess.run(cmd, cwd=request.cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: str | os.PathLike[str], task: str) -> tuple[str, int]:
        """Return a stub response for dry-run mode."""
        response = (
            f"[DRY-RUN] CodexRunner would have executed:\n"
            f"  role: {role}\n"
            f"  cwd: {cwd}\n"
            f"  task: {task[:100]}...\n"
            f"\n"
            f"Dry-run stub response: Task acknowledged."
        )
        logger.info("CodexRunner dry-run: role=%s, cwd=%s", role, cwd)
        return response, 0
