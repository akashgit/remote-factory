"""Failure tracker — classifies and groups rollout failure modes across training."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import structlog

from factory.skillopt.types import RolloutResult

log = structlog.get_logger()


class FailureMode:
    NO_CHANGE = "no_change"
    TIMEOUT = "timeout"
    LOCALIZATION_MISS = "localization_miss"
    WRONG_PATCH = "wrong_patch"
    TEST_REGRESSION = "test_regression"
    BUILD_ERROR = "build_error"
    EMPTY_TRACE = "empty_trace"
    UNKNOWN = "unknown"


_ALL_MODES = [
    FailureMode.NO_CHANGE,
    FailureMode.TIMEOUT,
    FailureMode.LOCALIZATION_MISS,
    FailureMode.WRONG_PATCH,
    FailureMode.TEST_REGRESSION,
    FailureMode.BUILD_ERROR,
    FailureMode.EMPTY_TRACE,
    FailureMode.UNKNOWN,
]


def classify_failure(result: RolloutResult) -> str:
    """Classify a failed rollout into a failure mode based on available signals."""
    if result.hard == 1.0:
        return ""

    trace = result.extras.get("trace_dump", "")
    fail = result.fail_reason

    if not trace and not fail:
        return FailureMode.EMPTY_TRACE

    trace_lower = trace.lower()
    fail_lower = fail.lower()

    if "timeout" in fail_lower or "timed out" in trace_lower or "timeoutexpired" in trace_lower:
        return FailureMode.TIMEOUT

    if "importerror" in trace_lower or "syntaxerror" in trace_lower or "modulenotfounderror" in trace_lower:
        return FailureMode.BUILD_ERROR

    has_edits = "[edit]" in trace or "[write]" in trace
    has_verifier = "[VERIFIER TEST RESULTS]" in trace

    if not has_edits:
        return FailureMode.NO_CHANGE

    if has_verifier:
        passed_count = trace_lower.count("passed")
        failed_count = trace_lower.count("failed")
        if failed_count > 0 and passed_count > 0:
            return FailureMode.TEST_REGRESSION
        if failed_count > 0:
            return FailureMode.WRONG_PATCH

    if "tests failed" in fail_lower or "failed" in fail_lower:
        return FailureMode.WRONG_PATCH

    if has_edits:
        return FailureMode.WRONG_PATCH

    return FailureMode.UNKNOWN


class FailureTracker:
    """Tracks failure modes across training steps, persisted to disk."""

    def __init__(self, out_dir: str | Path) -> None:
        self.out_dir = Path(out_dir)
        self.ledger_path = self.out_dir / "failure_ledger.json"
        self.entries: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.ledger_path.exists():
            try:
                self.entries = json.loads(self.ledger_path.read_text())
            except (json.JSONDecodeError, OSError):
                self.entries = []

    def _save(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps(self.entries, indent=2))

    def record_rollout(
        self,
        results: list[RolloutResult],
        global_step: int,
        phase: str,
    ) -> dict[str, list[str]]:
        """Record results from a rollout. Returns {failure_mode: [instance_ids]}."""
        grouped: dict[str, list[str]] = {}
        for r in results:
            mode = classify_failure(r)
            if not mode:
                continue
            grouped.setdefault(mode, []).append(r.id)
            self.entries.append({
                "instance_id": r.id,
                "global_step": global_step,
                "phase": phase,
                "mode": mode,
                "hard": r.hard,
                "fail_reason": r.fail_reason[:200],
            })
        self._save()

        total_failed = sum(len(ids) for ids in grouped.values())
        total_passed = len(results) - total_failed
        log.info(
            "failure tracking",
            phase=phase,
            step=global_step,
            passed=total_passed,
            failed=total_failed,
            modes={m: len(ids) for m, ids in grouped.items()},
        )
        return grouped

    def summary(self) -> dict[str, Any]:
        """Return aggregate failure mode statistics."""
        by_mode: dict[str, int] = Counter()
        by_instance: dict[str, Counter] = {}
        by_phase: dict[str, Counter] = {}

        for e in self.entries:
            mode = e["mode"]
            by_mode[mode] += 1
            by_instance.setdefault(e["instance_id"], Counter())[mode] += 1
            by_phase.setdefault(e["phase"], Counter())[mode] += 1

        always_fail = [
            iid for iid, modes in by_instance.items()
            if sum(modes.values()) == len([
                e for e in self.entries if e["instance_id"] == iid
            ])
        ]

        return {
            "total_failures": len(self.entries),
            "by_mode": dict(by_mode),
            "by_phase": {p: dict(c) for p, c in by_phase.items()},
            "always_fail_count": len(always_fail),
            "always_fail_top": sorted(
                always_fail,
                key=lambda iid: sum(by_instance[iid].values()),
                reverse=True,
            )[:20],
        }

    def print_summary(self) -> None:
        s = self.summary()
        lines = [
            f"=== Failure Tracker Summary ({s['total_failures']} total failures) ===",
            "",
            "By failure mode:",
        ]
        for mode in _ALL_MODES:
            count = s["by_mode"].get(mode, 0)
            if count:
                lines.append(f"  {mode:25s} {count}")

        lines.append("")
        lines.append("By phase:")
        for phase, modes in sorted(s["by_phase"].items()):
            total = sum(modes.values())
            lines.append(f"  {phase}: {total} failures")
            for mode in _ALL_MODES:
                count = modes.get(mode, 0)
                if count:
                    lines.append(f"    {mode:23s} {count}")

        if s["always_fail_top"]:
            lines.append("")
            lines.append(f"Consistently failing instances ({s['always_fail_count']} total):")
            for iid in s["always_fail_top"][:10]:
                lines.append(f"  {iid}")

        log.info("failure_summary", summary="\n".join(lines))
        print("\n".join(lines))
