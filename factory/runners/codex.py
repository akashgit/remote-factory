"""CodexRunner — OpenAI Codex CLI backend implementation.

Verified against codex-cli 0.136.0. Key differences from documentation:
- ``codex exec`` does NOT support ``--ask-for-approval`` — only the interactive
  ``codex`` command does. Use ``--sandbox workspace-write`` for headless.
- ``--json`` produces JSONL events (thread.started, turn.started, item.completed,
  turn.completed) — not a single JSON blob.
- Usage data (input_tokens, output_tokens, cached_input_tokens) is available in
  the ``turn.completed`` event's ``usage`` field.
- ``--dangerously-bypass-approvals-and-sandbox`` is the nuclear permission option.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.abstraction import (
    AgentRunner,
    Capability,
    Request,
    Response,
    RunnerIdentity,
)

logger = logging.getLogger(__name__)

_auth_checked = False


class CodexAuthError(Exception):
    """Raised when neither CODEX_API_KEY nor OPENAI_API_KEY is set."""

    def __init__(self) -> None:
        super().__init__(
            "CODEX_API_KEY (or OPENAI_API_KEY) environment variable is not set. "
            "Set it directly or add it to a config.toml credential profile: "
            "[credentials.codex] CODEX_API_KEY = \"...\""
        )


def _check_auth() -> None:
    """Check that codex auth is configured (env var or file-based).

    Codex supports multiple auth modes:
    - CODEX_API_KEY or OPENAI_API_KEY environment variables
    - File-based auth via ``codex login`` (stored in ~/.codex/auth.json)
    """
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        _auth_checked = True
        return
    # Check for file-based auth (codex login)
    auth_file = Path.home() / ".codex" / "auth.json"
    if auth_file.exists():
        _auth_checked = True
        return
    raise CodexAuthError()


def _make_codex_env() -> dict[str, str]:
    """Build subprocess env: strip VIRTUAL_ENV, ensure OPENAI_API_KEY is set."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if "OPENAI_API_KEY" not in env and "CODEX_API_KEY" in env:
        env["OPENAI_API_KEY"] = env["CODEX_API_KEY"]
    return env


def is_codex_dry_run() -> bool:
    """Return True if Codex dry-run mode is enabled."""
    from factory.user_config import resolve

    val = resolve("codex_dry_run", env_var="FACTORY_CODEX_DRY_RUN") or ""
    return val.lower() in ("1", "true", "yes")


_CODEX_IDENTITY = RunnerIdentity(
    name="codex",
    cli_command="codex",
    capabilities=frozenset({
        Capability.MODEL_OVERRIDE,
        Capability.INTERACTIVE,
        Capability.SANDBOXING,
        Capability.STRUCTURED_OUTPUT,
        Capability.USAGE_TRACKING,
    }),
)


def _parse_codex_jsonl(raw: str) -> tuple[str, "AgentUsage | None"]:
    """Parse Codex JSONL output into (text, usage).

    Codex --json emits lines like:
        {"type":"thread.started","thread_id":"..."}
        {"type":"turn.started"}
        {"type":"item.completed","item":{"id":"...","type":"agent_message","text":"..."}}
        {"type":"turn.completed","usage":{"input_tokens":N,...}}

    We extract text from item.completed events and usage from turn.completed.
    """
    from factory.models import AgentUsage

    texts: list[str] = []
    usage = None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "item.completed":
            item = event.get("item", {})
            text = item.get("text", "")
            if text:
                texts.append(text)

        elif event_type == "turn.completed":
            usage_block = event.get("usage", {})
            if usage_block:
                usage = AgentUsage(
                    input_tokens=usage_block.get("input_tokens", 0),
                    output_tokens=usage_block.get("output_tokens", 0),
                    cache_read_tokens=usage_block.get("cached_input_tokens", 0),
                    cache_creation_tokens=0,
                    total_cost_usd=0.0,
                    duration_ms=0.0,
                    num_turns=1,
                    model="",
                )

    return "\n".join(texts) if texts else raw, usage


class CodexRunner(AgentRunner):
    """Runner implementation for OpenAI Codex CLI.

    Verified against codex-cli 0.136.0.
    """

    name: str = "codex"

    @property
    def identity(self) -> RunnerIdentity:
        return _CODEX_IDENTITY

    def _build_command(self, request: Request) -> list[str]:
        """Build the codex exec command.

        The prompt is included as the positional argument to ``codex exec``.
        Tool filtering, effort, and append_system_prompt are proxied via
        prompt injection — folded into the prompt text before passing.

        Note: ``codex exec`` does NOT support ``--ask-for-approval``. Only
        ``--sandbox`` and ``--dangerously-bypass-approvals-and-sandbox`` work.
        """
        # Build prompt with proxied features injected
        full_prompt = request.prompt
        full_prompt = self._inject_tool_restrictions(full_prompt, request)
        full_prompt = self._inject_effort_instructions(full_prompt, request.effort)
        full_prompt = self._inject_append_system_prompt(full_prompt, request.append_system_prompt)
        full_prompt = f"{full_prompt}\n\n---\n\n## Current Task\n\n{request.task}"

        cmd = ["codex", "exec", full_prompt]

        # Permission handling — codex exec only supports --sandbox and
        # --dangerously-bypass-approvals-and-sandbox (NOT --ask-for-approval)
        if request.permission_mode == "bypassPermissions":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif request.skip_permissions:
            cmd.extend(["--sandbox", "workspace-write"])

        if request.model:
            cmd.extend(["--model", request.model])

        # Structured output via JSONL
        cmd.append("--json")

        return cmd

    def _build_env(self) -> dict[str, str]:
        return _make_codex_env()

    def _parse_response(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
    ) -> Response:
        """Parse codex JSONL output into a Response with usage tracking."""
        text, usage = _parse_codex_jsonl(stdout)
        return Response(stdout=text, return_code=return_code, usage=usage)

    def _warn_unsupported(self, request: Request) -> None:
        """Log warnings for features that cannot be proxied."""
        if request.max_budget_usd is not None:
            logger.warning(
                "CodexRunner: max_budget_usd=%.2f accepted but not natively enforced",
                request.max_budget_usd,
            )
        if request.mcp_config:
            logger.warning("CodexRunner: mcp_config is not supported by codex, ignoring")

    async def run(self, request: Request) -> Response:
        """Override for dry-run detection and auth check."""
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(request.role, request.cwd, request.task)
            return Response(stdout=stdout, return_code=code)

        _check_auth()
        return await super().run(request)

    # -- Backward-compatible headless() shim --

    async def headless(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        timeout: float = 600.0,
        model: str | None = None,
        dangerously_skip_permissions: bool = True,
        role: str = "unknown",
        session_name: str | None = None,
        tmux_persist: bool = False,
    ) -> tuple[str, int, "AgentUsage | None"]:
        """Run a headless Codex CLI invocation (backward-compat shim)."""
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with codex runner")

        request = Request(
            prompt=prompt,
            task=task,
            cwd=cwd,
            timeout=timeout,
            model=model,
            skip_permissions=dangerously_skip_permissions,
            role=role,
        )
        response = await self.run(request)
        return response.stdout, response.return_code, response.usage

    def interactive_run(
        self,
        prompt: str,
        task: str,
        cwd: Path,
        *,
        model: str | None = None,
        role: str = "ceo",
        dangerously_skip_permissions: bool = False,
        session_name: str | None = None,
    ) -> int:
        """Run an interactive Codex CLI session as a subprocess."""
        _ = role, session_name

        if is_codex_dry_run():
            print("[DRY-RUN] Would exec: codex (interactive)")
            print(f"[DRY-RUN] Task: {task[:200]}...")
            return 0

        _check_auth()

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        # Interactive codex command DOES support --ask-for-approval
        cmd = ["codex", full_prompt]

        if dangerously_skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")

        if model:
            cmd.extend(["--model", model])

        logger.info("CodexRunner interactive_run: cwd=%s", cwd)

        env = _make_codex_env()
        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: str | Path, task: str) -> tuple[str, int]:
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
