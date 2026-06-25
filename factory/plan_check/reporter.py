"""Generate verification reports in JSON and markdown formats."""

from __future__ import annotations

import json
from pathlib import Path

from factory.plan_check.models import (
    CriterionResult,
    HypothesisVerdict,
    VerificationReport,
)


def _criterion_sort_key(cr: CriterionResult) -> tuple[int, str]:
    if not cr.passed and cr.error is None:
        return (0, cr.criterion.criterion_id)
    if cr.error is not None:
        return (1, cr.criterion.criterion_id)
    return (2, cr.criterion.criterion_id)


def generate_report(report: VerificationReport) -> VerificationReport:
    sorted_hypotheses: list[HypothesisVerdict] = []
    for verdict in report.hypotheses:
        sorted_criteria = sorted(verdict.criteria, key=_criterion_sort_key)
        sorted_hypotheses.append(
            verdict.model_copy(update={"criteria": sorted_criteria})
        )
    sorted_hypotheses.sort(key=lambda v: (v.passed, v.hypothesis_id))
    return report.model_copy(update={"hypotheses": sorted_hypotheses})


def to_markdown(report: VerificationReport) -> str:
    lines: list[str] = []
    lines.append("## Acceptance Criteria Verification Report\n")
    s = report.summary
    lines.append(
        f"**Total:** {s.total_criteria} | "
        f"**Passed:** {s.passed_criteria} | "
        f"**Failed:** {s.failed_criteria} | "
        f"**Errors:** {s.error_criteria} | "
        f"**Pass rate:** {s.pass_rate:.0%}\n"
    )

    for verdict in report.hypotheses:
        status = "PASS" if verdict.passed else "FAIL"
        passed_count = sum(1 for c in verdict.criteria if c.passed)
        total_count = len(verdict.criteria)
        lines.append(
            f"### {verdict.hypothesis_id}: {verdict.hypothesis_title} "
            f"— {status} ({passed_count}/{total_count} criteria satisfied)\n"
        )
        for cr in verdict.criteria:
            if cr.passed:
                lines.append(f"- [x] {cr.criterion.description}")
            else:
                lines.append(f"- [ ] {cr.criterion.description}")
                if cr.expected_value:
                    lines.append(f"  - **Expected:** {cr.expected_value}")
                if cr.actual_value:
                    lines.append(f"  - **Actual:** {cr.actual_value}")
                if cr.evidence:
                    lines.append("  - **Evidence:**")
                    for e in cr.evidence:
                        lines.append(f"    - {e}")
                if cr.error:
                    lines.append(f"  - **Error:** {cr.error}")
        lines.append("")

    unsatisfied = [
        cr
        for verdict in report.hypotheses
        for cr in verdict.criteria
        if not cr.passed
    ]
    if unsatisfied:
        lines.append("### Unsatisfied Acceptance Criteria\n")
        for cr in unsatisfied:
            lines.append(
                f"- **{cr.criterion.criterion_id}**: {cr.criterion.description}"
            )
        lines.append("")

    if s.all_passed:
        lines.append("**VERDICT: ALL CRITERIA MET**")
    else:
        n = s.failed_criteria + s.error_criteria
        ids = ", ".join(s.failed_hypotheses)
        lines.append(
            f"**VERDICT: {n} CRITERIA UNSATISFIED — REDIRECT NEEDED** ({ids})"
        )

    return "\n".join(lines)


def to_json(report: VerificationReport) -> str:
    data = report.model_dump(mode="python")
    s = report.summary
    data["all_passed"] = s.all_passed
    data["pass_rate"] = s.pass_rate
    data["redirect_needed"] = not s.all_passed
    unsatisfied: list[dict[str, str | None]] = []
    for verdict in report.hypotheses:
        for cr in verdict.criteria:
            if not cr.passed:
                unsatisfied.append(
                    {
                        "hypothesis_id": verdict.hypothesis_id,
                        "criterion_id": cr.criterion.criterion_id,
                        "description": cr.criterion.description,
                        "expected": cr.expected_value,
                        "actual": cr.actual_value,
                    }
                )
    data["unsatisfied_criteria"] = unsatisfied
    return json.dumps(data, indent=2)


def write_report(
    report: VerificationReport, output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "acceptance-verification.md"
    json_path = output_dir / "acceptance-verification.json"
    md_path.write_text(to_markdown(report))
    json_path.write_text(to_json(report))
    return md_path, json_path
