"""CEO completion guard — auto-resume on premature exit.

Detects when the CEO exits before all planned work is complete and re-spawns
with a continuation task. This is a structural fix for model-side decisions
to "wrap up" early.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from factory.events import emit_event, load_events

log = structlog.get_logger()

# Hard cap on re-spawns per cycle (env-overridable)
DEFAULT_MAX_RESPAWNS = 5


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


def _count_verdicts(project_path: Path) -> int:
    """Count verdict.json files in .factory/experiments/*/."""
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


def _detect_incomplete(project_path: Path, mode: str) -> IncompleteGap | None:
    """Detect if the cycle is incomplete for the given mode.

    Returns IncompleteGap if work is incomplete, None if complete.
    """
    if mode in ("improve", "meta"):
        planned = _count_hypotheses(project_path)
        completed = _count_verdicts(project_path)

        if planned == 0:
            # No strategy yet — not an incomplete improve cycle, probably discover mode
            return None

        if completed >= planned:
            return None

        next_h = completed + 1
        return IncompleteGap(
            mode=mode,
            planned=planned,
            completed=completed,
            next_item=f"H{next_h}",
            reason=f"improve.incomplete: {completed}/{planned} hypotheses have verdicts",
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
        completed = _count_verdicts(project_path)

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


def _build_continuation_task(gap: IncompleteGap) -> str:
    """Build the continuation task string for re-spawning the CEO."""
    if gap.mode in ("improve", "meta"):
        return (
            f"Resume execution from hypothesis {gap.next_item}. "
            f"Strategy is already approved at .factory/strategy/current.md — "
            f"do not re-plan, do not re-run Researcher or Strategist. "
            f"Spawn Builder for {gap.next_item} immediately. "
            f"Progress so far: {gap.completed}/{gap.planned} hypotheses have verdicts."
        )
    elif gap.mode == "build":
        return (
            f"Resume Build pipeline from {gap.next_item}. "
            f"Plan is already approved at .factory/strategy/current.md. "
            f"Progress so far: {gap.completed}/{gap.planned} phases complete. "
            f"Continue with the next phase immediately."
        )
    elif gap.mode == "discover":
        return (
            "Resume Discovery. The eval profile has not been generated yet. "
            "Complete the Discover mode workflow to produce .factory/eval_profile.json."
        )
    return f"Resume from {gap.next_item}."


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

    task = initial_task
    final_output = ""
    gap: IncompleteGap | None = None

    for attempt in range(max_respawns + 1):
        log.info("ceo_spawn", attempt=attempt, task_preview=task[:100])

        result, code = await invoke_agent(
            "ceo", task, project_path,
            timeout=timeout, model=model, runner_name=runner_name,
        )
        final_output = result

        # User interrupt — respect it
        if code in (130, 143) or code > 128:
            log.info("ceo_user_interrupt", code=code)
            return result, code

        # Explicit ABORT — respect it
        if _has_aborted(project_path):
            log.info("ceo_aborted", reason="cycle.aborted event found")
            return result, code

        # Check for incomplete work
        gap = _detect_incomplete(project_path, mode)
        if gap is None:
            log.info("ceo_complete", attempt=attempt)
            return result, code

        # Check budget before re-spawning
        if not _budget_allows_respawn(runner_name, project_path):
            log.warning("ceo_budget_exceeded", gap=gap)
            _write_cycle_incomplete(project_path, gap, "budget_exceeded")
            return result, 1

        # Emit respawn event
        emit_event(
            project_path,
            "ceo.respawn",
            agent="ceo",
            data={
                "attempt": attempt + 1,
                "reason": gap.reason,
                "planned": gap.planned,
                "completed": gap.completed,
                "next": gap.next_item,
            },
        )

        # Build continuation task
        task = _build_continuation_task(gap)
        log.info("ceo_respawn", attempt=attempt + 1, next_item=gap.next_item)

    # Cap hit
    if gap:
        log.warning("ceo_respawn_cap_hit", attempts=max_respawns + 1, gap=gap)
        _write_cycle_incomplete(project_path, gap, "respawn_cap_hit")

    return final_output, 1
