"""QA Agent integration helpers for plan-check reports."""

from __future__ import annotations

from factory.plan_check.models import VerificationReport


def format_for_qa(report: VerificationReport) -> str:
    lines: list[str] = []
    lines.append("#### Acceptance Criteria Verification\n")

    s = report.summary
    lines.append(
        f"**{s.passed_criteria}/{s.total_criteria}** criteria satisfied "
        f"({s.pass_rate:.0%} pass rate)\n"
    )

    unsatisfied = [
        (verdict.hypothesis_id, cr)
        for verdict in report.hypotheses
        for cr in verdict.criteria
        if not cr.passed
    ]

    if unsatisfied:
        lines.append("**UNSATISFIED CRITERIA:**\n")
        for hyp_id, cr in unsatisfied:
            lines.append(f"- **{hyp_id} / {cr.criterion.criterion_id}**: {cr.criterion.description}")
            if cr.expected_value and cr.actual_value:
                lines.append(f"  - Expected: {cr.expected_value} | Actual: {cr.actual_value}")
            elif cr.error:
                lines.append(f"  - Error: {cr.error}")
        lines.append("")
    else:
        lines.append("All acceptance criteria satisfied.\n")

    lines.append("*Run `factory plan-check $PROJECT_PATH --json` for full details.*")

    return "\n".join(lines)


def get_redirect_payload(report: VerificationReport) -> dict:
    unsatisfied: list[dict[str, str | None]] = []
    for verdict in report.hypotheses:
        for cr in verdict.criteria:
            if not cr.passed:
                unsatisfied.append({
                    "hypothesis_id": verdict.hypothesis_id,
                    "criterion": cr.criterion.description,
                    "expected": cr.expected_value,
                    "actual": cr.actual_value,
                })

    parts: list[str] = []
    if unsatisfied:
        parts.append(
            f"{len(unsatisfied)} acceptance criteria unsatisfied across "
            f"{len(report.summary.failed_hypotheses)} hypothesis(es): "
            f"{', '.join(report.summary.failed_hypotheses)}."
        )
        for item in unsatisfied:
            detail = f"  - {item['hypothesis_id']}: {item['criterion']}"
            if item.get("expected") and item.get("actual"):
                detail += f" (expected {item['expected']}, got {item['actual']})"
            parts.append(detail)

    return {
        "unsatisfied": unsatisfied,
        "redirect_message": "\n".join(parts) if parts else "All criteria satisfied.",
    }
