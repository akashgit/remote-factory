"""Runner v2 types — dataclasses for the runner abstraction layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# -- Capabilities ------------------------------------------------------------

class RunnerCapability(Enum):
    MODEL_OVERRIDE = "model_override"
    SESSION_RESUME = "session_resume"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    INTERACTIVE = "interactive"
    DRY_RUN = "dry_run"
    SANDBOXING = "sandboxing"
    ACP = "acp"
    EXECUTION_TRACE = "execution_trace"


@dataclass
class RunnerInfo:
    name: str                                    # "claude", "codex", "opencode"
    display_name: str                            # "Claude Code", "OpenCode"
    version: str | None = None
    capabilities: set[RunnerCapability] = field(default_factory=set)


# -- Request ------------------------------------------------------------------

@dataclass
class RunnerRequest:
    system_prompt: str                           # Agent role definition (system prompt + playbook)
    task: str                                    # User-facing task description
    cwd: str                                     # Working directory
    timeout: int = 300
    model: str | None = None
    session_name: str | None = None
    role: str | None = None                      # Agent role (for logging prefix)
    skip_permissions: bool = True
    env_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        """Fully assembled prompt (system + task). For runners that don't separate them."""
        return f"{self.system_prompt}\n\n---\n\n## Current Task\n\n{self.task}"


# -- Usage --------------------------------------------------------------------

@dataclass
class UsageStats:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    duration_seconds: float | None = None
    model_used: str | None = None


# -- Execution Trace ----------------------------------------------------------

class ToolKind(Enum):
    """Aligned with ACP's ToolKind for interop."""
    READ = "read"
    EDIT = "edit"
    DELETE = "delete"
    SEARCH = "search"
    EXECUTE = "execute"
    THINK = "think"
    FETCH = "fetch"
    OTHER = "other"


class ToolCallStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class FileLocation:
    path: str
    line: int | None = None


@dataclass
class ToolCallTrace:
    """One tool invocation. The atom of observability."""
    tool_name: str                               # "Read", "Bash", "Edit"
    kind: ToolKind
    status: ToolCallStatus = ToolCallStatus.COMPLETED
    input_summary: str | None = None             # Truncated (first 200 chars)
    output_summary: str | None = None
    locations: list[FileLocation] = field(default_factory=list)
    duration_ms: int | None = None
    error: str | None = None


@dataclass
class AgentStep:
    """One LLM inference round (turn/step).

    Maps to: Claude assistant+user event pair, Codex turn, OpenCode step.
    """
    step_index: int
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    reasoning: str | None = None
    output_text: str | None = None
    usage: UsageStats | None = None


@dataclass
class ExecutionTrace:
    """Full execution trace from a runner invocation."""
    steps: list[AgentStep] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    thinking_blocks: list[str] = field(default_factory=list)
    sub_agent_traces: list[ExecutionTrace] = field(default_factory=list)


# -- Response -----------------------------------------------------------------

@dataclass
class RunnerResponse:
    output: str
    exit_code: int
    usage: UsageStats | None = None
    trace: ExecutionTrace | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
