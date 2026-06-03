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
    TOOL_CONTROL = "tool_control"
    MAX_TURNS = "max_turns"


# -- Permission & Sandbox Modes ---------------------------------------------

class PermissionMode(Enum):
    """How the agent handles permission prompts."""
    AUTO = "auto"                        # Skip all prompts (headless)
    APPROVE_WRITES = "approve_writes"    # Auto-approve reads, prompt for writes
    APPROVE_ALL = "approve_all"          # Prompt for everything (interactive default)


class SandboxMode(Enum):
    """Workspace access level for the agent."""
    NONE = "none"                        # No sandbox restrictions
    READ_ONLY = "read_only"              # Can read but not write files
    WORKSPACE_WRITE = "workspace_write"  # Can write in workspace only
    FULL = "full"                        # Full filesystem access


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
    env_overrides: dict[str, str] = field(default_factory=dict)

    # -- System Prompt Management --
    system_prompt_append: list[str] = field(default_factory=list)
    system_prompt_files: list[str] = field(default_factory=list)

    # -- Permission & Tool Control --
    permission_mode: PermissionMode = PermissionMode.AUTO
    allowed_tools: list[str] | None = None       # Whitelist: ["Read", "Grep", "Glob"]
    disallowed_tools: list[str] | None = None    # Blacklist: ["Bash"]
    sandbox_mode: SandboxMode | None = None

    # -- Resource Limits --
    max_turns: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None

    # -- Deprecated (use permission_mode instead) --
    skip_permissions: bool = True

    def append_system_prompt(self, text: str) -> None:
        """Append an additional section to the system prompt."""
        self.system_prompt_append.append(text)

    @property
    def full_system_prompt(self) -> str:
        """System prompt with all appended sections and file contents joined."""
        parts = [self.system_prompt]
        parts.extend(self.system_prompt_append)
        for fpath in self.system_prompt_files:
            try:
                with open(fpath) as f:
                    parts.append(f.read())
            except OSError:
                pass
        return "\n\n".join(parts)

    @property
    def prompt(self) -> str:
        """Fully assembled prompt (system + appends + task). For runners that inline everything."""
        return f"{self.full_system_prompt}\n\n---\n\n## Current Task\n\n{self.task}"


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
