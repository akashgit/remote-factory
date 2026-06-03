"""CodexRunner — OpenAI Codex CLI backend implementation (v2).

Verified against codex-cli 0.136.0. Key findings from real CLI testing:
- `codex exec` defaults to approval=never (no --ask-for-approval flag needed/available)
- `--sandbox` values: read-only, workspace-write, danger-full-access
- `--json` gives JSONL output with thread.started, turn.started, item.completed, turn.completed
- `--cd` sets working directory (requires --skip-git-repo-check for non-git dirs)
- `--model` / `-m` for model override
- Auth uses `codex login` (not env vars), but OPENAI_API_KEY works as fallback
- `--dangerously-bypass-approvals-and-sandbox` for full unrestricted mode
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

from factory.runners._stream import should_stream, stream_subprocess
from factory.runners.cli_adapter import CLIAdapter
from factory.runners.types import (
    AgentStep,
    ExecutionTrace,
    RunnerCapability,
    RunnerRequest,
    RunnerResponse,
    SandboxMode,
    UsageStats,
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
    """Check that CODEX_API_KEY or OPENAI_API_KEY is set (once per process)."""
    global _auth_checked  # noqa: PLW0603
    if _auth_checked:
        return
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
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


# -- Sandbox mode mapping (verified against codex-cli 0.136.0) ----------------

_SANDBOX_MAP: dict[SandboxMode, str] = {
    SandboxMode.READ_ONLY: "read-only",
    SandboxMode.WORKSPACE_WRITE: "workspace-write",
    SandboxMode.FULL: "danger-full-access",
    # SandboxMode.NONE → use --dangerously-bypass-approvals-and-sandbox
}


# -- JSONL output parser -------------------------------------------------------

def _parse_codex_jsonl(jsonl_text: str) -> tuple[str, UsageStats | None, ExecutionTrace | None]:
    """Parse Codex --json JSONL output.

    Events:
    - thread.started: {thread_id}
    - turn.started: {}
    - item.completed: {item: {id, type, text}}  (type=agent_message for text output)
    - turn.completed: {usage: {input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens}}
    """
    final_text = ""
    usage: UsageStats | None = None
    trace = ExecutionTrace()
    step_index = 0

    for line in jsonl_text.splitlines():
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
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    final_text = text
                    step = AgentStep(step_index=step_index, output_text=text)
                    trace.steps.append(step)
                    step_index += 1

        elif event_type == "turn.completed":
            usage_block = event.get("usage", {})
            if usage_block:
                input_tokens = usage_block.get("input_tokens")
                output_tokens = usage_block.get("output_tokens")
                usage = UsageStats(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=(
                        (input_tokens or 0) + (output_tokens or 0)
                    ) or None,
                )

    return final_text, usage, trace if trace.steps else None


class CodexRunner(CLIAdapter):
    """Runner implementation for OpenAI Codex CLI.

    Verified against codex-cli 0.136.0.
    """

    name: str = "codex"

    def __init__(self) -> None:
        super().__init__(
            name="codex",
            display_name="OpenAI Codex",
            capabilities={
                RunnerCapability.MODEL_OVERRIDE,
                RunnerCapability.SANDBOXING,
                RunnerCapability.STRUCTURED_OUTPUT,
            },
            binary="codex",
        )

    async def check_health(self) -> tuple[bool, str]:
        ok, msg = await super().check_health()
        if not ok:
            return ok, msg
        # Codex uses `codex login` for auth — env vars are optional fallback
        # Don't fail health check on missing env vars since codex has its own auth
        return True, "codex found"

    def _inject_prompt_proxy(self, request: RunnerRequest) -> str:
        """Codex handles sandbox natively — proxy tool control and limits."""
        parts: list[str] = []

        # Tool control: no native flag — prompt proxy
        if request.allowed_tools:
            tools = ", ".join(request.allowed_tools)
            parts.append(f"IMPORTANT: You may ONLY use these tools: {tools}. Do not use any other tools.")
        if request.disallowed_tools:
            tools = ", ".join(request.disallowed_tools)
            parts.append(f"IMPORTANT: You must NOT use these tools: {tools}.")

        # Resource limits: no native flags — prompt proxy
        if request.max_turns is not None:
            parts.append(
                f"IMPORTANT: Complete your work within {request.max_turns} conversation turns."
            )
        if request.max_tokens is not None:
            parts.append(
                f"IMPORTANT: Keep your total output under {request.max_tokens} tokens. Be concise."
            )
        if request.max_cost_usd is not None:
            parts.append(
                f"IMPORTANT: This invocation has a budget of ${request.max_cost_usd:.2f}. "
                "Minimize token usage."
            )

        return "\n\n".join(parts)

    def _build_command(
        self,
        request: RunnerRequest,
        *,
        prompt_file: str | None = None,
    ) -> list[str]:
        cmd = ["codex", "exec", request.prompt]  # .prompt combines system+task

        # Sandbox mode (native)
        if request.sandbox_mode == SandboxMode.NONE:
            # No sandbox restriction — bypass everything
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            sandbox = _SANDBOX_MAP.get(
                request.sandbox_mode or SandboxMode.WORKSPACE_WRITE,
                "workspace-write",
            )
            cmd.extend(["--sandbox", sandbox])

        # JSONL output for structured parsing
        cmd.append("--json")

        # Model override
        if request.model:
            cmd.extend(["--model", request.model])

        return cmd

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> RunnerResponse:
        final_text, usage, trace = _parse_codex_jsonl(stdout)
        return RunnerResponse(
            output=final_text or stdout,
            exit_code=exit_code,
            usage=usage,
            trace=trace,
        )

    def _build_env(self, request: RunnerRequest) -> dict[str, str]:
        env = super()._build_env(request)
        codex_key = env.get("CODEX_API_KEY")
        if codex_key and "OPENAI_API_KEY" not in env:
            env["OPENAI_API_KEY"] = codex_key
        return env

    # -- v1 backward-compat methods ----------------------------------------

    async def headless(  # type: ignore[override]
        self,
        prompt: str | RunnerRequest = "",
        task: str = "",
        cwd: Path | str = ".",
        *,
        timeout: float = 600.0,
        model: str | None = None,
        dangerously_skip_permissions: bool = True,
        role: str = "unknown",
        session_name: str | None = None,
        tmux_persist: bool = False,
    ) -> tuple[str, int, None] | RunnerResponse:
        """Run a headless Codex CLI invocation.

        Supports both v1 (positional args -> tuple) and v2 (RunnerRequest -> RunnerResponse).
        """
        if isinstance(prompt, RunnerRequest):
            if is_codex_dry_run():
                return RunnerResponse(
                    output="[DRY-RUN] CodexRunner dry-run stub response.",
                    exit_code=0,
                )
            return await CLIAdapter.headless(self, prompt)

        # v1 path
        _ = session_name
        if tmux_persist:
            logger.warning("tmux_persist not supported with codex runner")
        if is_codex_dry_run():
            stdout, code = self._dry_run_response(role, Path(str(cwd)), task)
            return stdout, code, None

        # Note: codex uses `codex login` for auth — env vars are optional fallback.
        # Don't fail here if env vars are missing; let codex itself handle auth errors.

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["codex", "exec", full_prompt]

        if dangerously_skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")

        if model:
            cmd.extend(["--model", model])

        logger.info("CodexRunner headless: cwd=%s, model=%s, role=%s", cwd, model, role)

        env = _make_codex_env()

        stream = should_stream()
        prefix = f"[codex:{role}]" if stream else None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                stream_subprocess(proc, stream=stream, prefix=prefix),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            await proc.wait()  # type: ignore[union-attr]
            logger.error("CodexRunner timed out after %ss", timeout)
            return f"Agent timed out after {timeout}s", 1, None
        except FileNotFoundError:
            logger.error("'codex' CLI not found on PATH")
            return "Error: 'codex' CLI not found on PATH", 1, None

        raw_stdout = stdout_bytes.decode()
        stderr_str = stderr_bytes.decode()

        if proc.returncode != 0:
            logger.error(
                "CodexRunner exited with code %d: stderr=%s stdout=%s",
                proc.returncode, stderr_str[:300], raw_stdout[:300],
            )
            import sys as _sys
            print(
                f"[codex:{role}] FAILED (exit={proc.returncode}): {stderr_str[:200]}",
                file=_sys.stderr,
            )
            # Include stderr in output so caller can see the error
            error_output = raw_stdout or stderr_str or f"codex exited with code {proc.returncode}"
            return error_output, proc.returncode or 1, None

        return raw_stdout, proc.returncode or 0, None

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

        full_prompt = f"{prompt}\n\n---\n\n## Current Task\n\n{task}"

        cmd = ["codex", full_prompt]

        if dangerously_skip_permissions:
            cmd.append("--dangerously-bypass-approvals-and-sandbox")

        if model:
            cmd.extend(["--model", model])

        logger.info("CodexRunner interactive_run: cwd=%s", cwd)

        env = _make_codex_env()
        result = subprocess.run(cmd, cwd=cwd, env=env)
        return result.returncode

    def _dry_run_response(self, role: str, cwd: Path, task: str) -> tuple[str, int]:
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
