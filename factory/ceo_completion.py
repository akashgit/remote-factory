"""CEO completion guard — auto-resume on premature exit.

Detects when the CEO exits before all planned work is complete and re-spawns
with a continuation task. This is a structural fix for model-side decisions
to "wrap up" early.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from factory.events import emit_event, load_events
from factory.models import CycleState

log = structlog.get_logger()

# Staleness threshold for cycle.json (24 hours)
CYCLE_STALENESS_HOURS = 24

# Hard cap on re-spawns per cycle (env-overridable)
DEFAULT_MAX_RESPAWNS = 5


# ── cycle state persistence ──────────────────────────────────────


def _cycle_state_path(project_path: Path) -> Path:
    """Return the path to .factory/state/cycle.json."""
    return project_path / ".factory" / "state" / "cycle.json"


def read_cycle_state(project_path: Path) -> CycleState | None:
    """Read in-flight cycle state if it exists and is non-stale.

    Returns None if:
    - cycle.json doesn't exist
    - cycle.json is malformed
    - cycle.json is stale (older than CYCLE_STALENESS_HOURS)
    """
    path = _cycle_state_path(project_path)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())

        # Parse datetime from ISO string (model_dump(mode="json") serializes as ISO)
        if "started_at" in data and isinstance(data["started_at"], str):
            data["started_at"] = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))

        state = CycleState.model_validate(data)

        # Check staleness
        now = datetime.now(timezone.utc)
        started = state.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_hours = (now - started).total_seconds() / 3600

        if age_hours > CYCLE_STALENESS_HOURS:
            log.info("cycle_state_stale", age_hours=age_hours, cycle_id=state.cycle_id)
            return None

        return state
    except (json.JSONDecodeError, ValueError) as e:
        log.warning("cycle_state_parse_error", error=str(e))
        return None


def write_cycle_state(project_path: Path, state: CycleState) -> None:
    """Write cycle state to .factory/state/cycle.json."""
    path = _cycle_state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use model_dump with mode="json" for proper datetime serialization
    data = state.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2))
    log.info("cycle_state_written", cycle_id=state.cycle_id, mode=state.mode, respawns=state.respawns)


def delete_cycle_state(project_path: Path) -> bool:
    """Delete cycle.json on cycle completion. Returns True if deleted."""
    path = _cycle_state_path(project_path)
    if path.exists():
        path.unlink()
        log.info("cycle_state_deleted", path=str(path))
        return True
    return False


def create_cycle_state(
    mode: str, initial_prompt: str = "", runner_name: str | None = None
) -> CycleState:
    """Create a new CycleState for a fresh cycle."""
    return CycleState(
        cycle_id=str(uuid.uuid4())[:8],
        started_at=datetime.now(timezone.utc),
        mode=mode,  # type: ignore[arg-type]
        initial_prompt=initial_prompt[:1000],  # Truncate to avoid bloat
        respawns=0,
        runner_name=runner_name,
    )


@dataclass
class IncompleteGap:
    """Describes what work is incomplete."""

    mode: str
    planned: int
    completed: int
    next_item: str
    reason: str


def _count_hypotheses(project_path: Path) -> int:
    """Count hypotheses in .factory/strategy/current.md."""
    strategy_file = project_path / ".factory" / "strategy" / "current.md"
    if not strategy_file.exists():
        return 0

    content = strategy_file.read_text()
    # Match headings like "#### H1:" or "### H2:" etc.
    matches = re.findall(r"^#{2,4}\s+H(\d+):", content, re.MULTILINE)
    return len(matches)


def _count_verdicts(project_path: Path, since_ts: datetime | None = None) -> int:
    """Count experiments with verdicts, optionally filtered by timestamp.

    If since_ts is provided, only counts experiments created after that time.
    This prevents counting stale experiments from previous cycles.

    Reads from results.tsv for timestamp-aware counting. Falls back to counting
    verdict.json files if results.tsv doesn't exist (backward compatibility).
    """
    import csv

    tsv_path = project_path / ".factory" / "results.tsv"

    # If results.tsv exists, use it for timestamp-aware counting
    if tsv_path.exists():
        count = 0
        with open(tsv_path, newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            for row in reader:
                verdict = row.get("verdict", "").strip().lower()
                if verdict not in ("keep", "revert", "error"):
                    continue

                if since_ts is not None:
                    try:
                        row_ts = datetime.fromisoformat(row["timestamp"])
                        if row_ts.tzinfo is None:
                            row_ts = row_ts.replace(tzinfo=timezone.utc)
                        since_ts_tz = since_ts
                        if since_ts_tz.tzinfo is None:
                            since_ts_tz = since_ts_tz.replace(tzinfo=timezone.utc)
                        if row_ts < since_ts_tz:
                            continue
                    except (KeyError, ValueError):
                        continue

                count += 1
        return count

    # Fallback: count verdict.json files (no timestamp filtering possible)
    # This path is used when results.tsv doesn't exist yet (e.g., fresh projects, tests)
    experiments_dir = project_path / ".factory" / "experiments"
    if not experiments_dir.exists():
        return 0

    count = 0
    for exp_dir in experiments_dir.iterdir():
        if exp_dir.is_dir() and (exp_dir / "verdict.json").exists():
            count += 1
    return count


def _has_eval_profile(project_path: Path) -> bool:
    """Check if .factory/eval_profile.json exists and is non-empty."""
    profile = project_path / ".factory" / "eval_profile.json"
    if not profile.exists():
        return False
    return profile.stat().st_size > 10  # More than just "{}"


def _has_aborted(project_path: Path, since_ts: str | None = None) -> bool:
    """Check if cycle.aborted event exists in events.jsonl."""
    events = load_events(project_path)
    for event in reversed(events):
        if event.get("type") == "cycle.aborted":
            if since_ts is None:
                return True
            if event.get("timestamp", "") >= since_ts:
                return True
    return False


def _detect_incomplete(
    project_path: Path, mode: str, cycle_started_at: datetime | None = None
) -> IncompleteGap | None:
    """Detect if the cycle is incomplete for the given mode.

    Returns IncompleteGap if work is incomplete, None if complete.

    Args:
        project_path: Path to the project.
        mode: CEO mode (improve, build, discover, meta).
        cycle_started_at: If provided, only counts experiments created after this time.
            This prevents counting stale experiments from previous cycles.
    """
    if mode in ("improve", "meta", "research"):
        planned = _count_hypotheses(project_path)
        completed = _count_verdicts(project_path, since_ts=cycle_started_at)

        if planned == 0:
            # No strategy yet — not an incomplete cycle, probably discover mode
            return None

        if completed >= planned:
            return None

        next_h = completed + 1
        reason_prefix = "research" if mode == "research" else "improve"
        return IncompleteGap(
            mode=mode,
            planned=planned,
            completed=completed,
            next_item=f"H{next_h}",
            reason=f"{reason_prefix}.incomplete: {completed}/{planned} hypotheses have verdicts",
        )

    elif mode == "discover":
        if _has_eval_profile(project_path):
            return None
        return IncompleteGap(
            mode=mode,
            planned=1,
            completed=0,
            next_item="eval_profile",
            reason="discover.incomplete: no eval_profile.json",
        )

    elif mode == "build":
        # For build mode, check if Builder completed at least one hypothesis
        # In build mode, the strategy file should have phases marked as hypotheses
        planned = _count_hypotheses(project_path)
        completed = _count_verdicts(project_path, since_ts=cycle_started_at)

        if planned == 0:
            # No strategy means we're in scaffold phase — check for eval profile
            if not _has_eval_profile(project_path):
                return IncompleteGap(
                    mode=mode,
                    planned=1,
                    completed=0,
                    next_item="discovery",
                    reason="build.incomplete: no eval profile yet",
                )
            return None

        if completed >= planned:
            return None

        next_h = completed + 1
        return IncompleteGap(
            mode=mode,
            planned=planned,
            completed=completed,
            next_item=f"Phase{next_h}",
            reason=f"build.incomplete: {completed}/{planned} phases have verdicts",
        )

    # Unknown mode — assume complete
    return None


def _build_continuation_task(gap: IncompleteGap, cycle_state: CycleState | None = None) -> str:
    """Build the continuation task string for re-spawning the CEO.

    Includes explicit mode directive to prevent mode flipping on respawn.
    """
    # Mode directive header — prevents mode flip on respawn
    mode_directive = (
        f"## CRITICAL: Mode Override\n\n"
        f"This is a CONTINUATION of an in-flight {gap.mode.upper()} cycle. "
        f"Do NOT re-detect mode. Do NOT switch to a different mode. "
        f"The cycle mode is **{gap.mode}** — execute {gap.mode.upper()} mode only.\n\n"
    )

    if cycle_state:
        mode_directive += (
            f"Cycle ID: {cycle_state.cycle_id}\n"
            f"Respawn count: {cycle_state.respawns}\n\n"
        )

    if gap.mode == "research":
        body = (
            f"Resume execution from hypothesis {gap.next_item}. "
            f"Strategy is already approved at .factory/strategy/current.md — "
            f"do not re-plan, do not re-run Failure Analyst, Researcher, or Strategist. "
            f"The baseline run (R0), failure analysis (R1), and research (R1.5) are already complete. "
            f"Spawn Builder for {gap.next_item} immediately, then continue the "
            f"research cycle (R3–R5) for each remaining hypothesis. "
            f"Progress so far: {gap.completed}/{gap.planned} hypotheses have verdicts."
        )
    elif gap.mode in ("improve", "meta"):
        body = (
            f"Resume execution from hypothesis {gap.next_item}. "
            f"Strategy is already approved at .factory/strategy/current.md — "
            f"do not re-plan, do not re-run Researcher or Strategist. "
            f"Spawn Builder for {gap.next_item} immediately. "
            f"Progress so far: {gap.completed}/{gap.planned} hypotheses have verdicts."
        )
    elif gap.mode == "build":
        body = (
            f"Resume Build pipeline from {gap.next_item}. "
            f"Plan is already approved at .factory/strategy/current.md. "
            f"Progress so far: {gap.completed}/{gap.planned} phases complete. "
            f"Continue with the next phase immediately."
        )
    elif gap.mode == "discover":
        body = (
            "Resume Discovery. The eval profile has not been generated yet. "
            "Complete the Discover mode workflow to produce .factory/eval_profile.json."
        )
    else:
        body = f"Resume from {gap.next_item}."

    return mode_directive + body


def _budget_allows_respawn(runner_name: str | None, project_path: Path) -> bool:
    """Check if budget/ceiling allows another spawn."""
    if runner_name == "bob":
        from factory.runners.usage import check_ceilings, CeilingExceededError
        from datetime import datetime, timezone

        try:
            check_ceilings(project_path, datetime.now(timezone.utc))
            return True
        except CeilingExceededError:
            return False

    # Claude runner has no ceiling
    return True


def _write_cycle_incomplete(project_path: Path, gap: IncompleteGap, reason: str) -> None:
    """Write .factory/strategy/cycle-incomplete.md describing what wasn't finished."""
    strategy_dir = project_path / ".factory" / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)

    incomplete_file = strategy_dir / "cycle-incomplete.md"
    content = f"""# Cycle Incomplete

**Mode:** {gap.mode}
**Reason:** {reason}
**Planned:** {gap.planned}
**Completed:** {gap.completed}
**Next item that wasn't started:** {gap.next_item}

## Details

{gap.reason}

This file is written when the CEO completion guard gives up after hitting
the respawn cap or budget limit. The cycle can be resumed manually with:

```bash
factory ceo /path/to/project --headless
```
"""
    incomplete_file.write_text(content)
    log.warning("cycle_incomplete", reason=reason, gap=gap)


async def run_ceo_with_completion_guard(
    project_path: Path,
    initial_task: str,
    *,
    mode: str,
    runner_name: str | None = None,
    model: str | None = None,
    timeout: float = 3600.0,
    max_respawns: int | None = None,
) -> tuple[str, int]:
    """Spawn CEO; if it exits with planned work undone, re-spawn until done or cap hit.

    Mode is persisted in .factory/state/cycle.json to prevent mode flipping across
    respawns. The cycle state is created on first spawn and deleted on completion.

    Args:
        project_path: Path to the project.
        initial_task: Initial task string for the CEO.
        mode: CEO mode (improve, build, discover, meta).
        runner_name: Runner to use (claude or bob).
        model: Optional model override.
        timeout: Timeout per CEO spawn in seconds.
        max_respawns: Max re-spawns (default from env or 5).

    Returns:
        (final_output, exit_code)
    """
    from factory.agents.runner import invoke_agent

    # Check escape hatch
    if os.environ.get("FACTORY_CEO_RESPAWN_DISABLED") == "1":
        log.info("ceo_respawn_disabled", reason="FACTORY_CEO_RESPAWN_DISABLED=1")
        return await invoke_agent(
            "ceo", initial_task, project_path,
            timeout=timeout, model=model, runner_name=runner_name,
        )

    if max_respawns is None:
        max_respawns = int(os.environ.get("FACTORY_CEO_MAX_RESPAWNS", DEFAULT_MAX_RESPAWNS))

    # Check for existing in-flight cycle (respawn scenario)
    cycle_state = read_cycle_state(project_path)
    if cycle_state:
        # Continuing an existing cycle — use its mode and runner, not the passed-in ones
        log.info(
            "cycle_state_found",
            cycle_id=cycle_state.cycle_id,
            original_mode=cycle_state.mode,
            passed_mode=mode,
        )
        mode = cycle_state.mode
        # Restore runner_name from persisted state (if set)
        if cycle_state.runner_name:
            runner_name = cycle_state.runner_name
    else:
        # Fresh cycle — create new state
        cycle_state = create_cycle_state(mode, initial_task, runner_name)
        write_cycle_state(project_path, cycle_state)
        log.info("cycle_state_created", cycle_id=cycle_state.cycle_id, mode=mode)

    task = initial_task
    final_output = ""
    gap: IncompleteGap | None = None

    for attempt in range(max_respawns + 1):
        log.info("ceo_spawn", attempt=attempt, task_preview=task[:100], mode=mode)

        result, code = await invoke_agent(
            "ceo", task, project_path,
            timeout=timeout, model=model, runner_name=runner_name,
        )
        final_output = result

        # User interrupt — respect it (but don't delete cycle state for later resume)
        if code in (130, 143) or code > 128:
            log.info("ceo_user_interrupt", code=code)
            return result, code

        # Explicit ABORT — respect it and clean up cycle state
        # Pass cycle start time to filter out stale abort events from previous cycles
        if _has_aborted(project_path, since_ts=cycle_state.started_at.isoformat()):
            log.info("ceo_aborted", reason="cycle.aborted event found")
            delete_cycle_state(project_path)
            return result, code

        # Check for incomplete work — pass cycle start time to filter stale experiments
        gap = _detect_incomplete(project_path, mode, cycle_started_at=cycle_state.started_at)
        if gap is None:
            log.info("ceo_complete", attempt=attempt)
            # Cycle complete — delete cycle state so next invocation starts fresh
            delete_cycle_state(project_path)
            return result, code

        # Check budget before re-spawning
        if not _budget_allows_respawn(runner_name, project_path):
            log.warning("ceo_budget_exceeded", gap=gap)
            _write_cycle_incomplete(project_path, gap, "budget_exceeded")
            return result, 1

        # Update cycle state with incremented respawn count
        cycle_state.respawns += 1
        write_cycle_state(project_path, cycle_state)

        # Emit respawn event with cycle_id
        emit_event(
            project_path,
            "ceo.respawn",
            agent="ceo",
            data={
                "attempt": attempt + 1,
                "cycle_id": cycle_state.cycle_id,
                "mode": mode,
                "reason": gap.reason,
                "planned": gap.planned,
                "completed": gap.completed,
                "next": gap.next_item,
            },
        )

        # Build continuation task with explicit mode directive
        task = _build_continuation_task(gap, cycle_state)
        log.info("ceo_respawn", attempt=attempt + 1, next_item=gap.next_item, mode=mode)

    # Cap hit — don't delete cycle state to allow manual resume
    if gap:
        log.warning("ceo_respawn_cap_hit", attempts=max_respawns + 1, gap=gap)
        _write_cycle_incomplete(project_path, gap, "respawn_cap_hit")

    return final_output, 1
