from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, computed_field


class AcceptanceCriterion(BaseModel):
    criterion_id: str
    hypothesis_id: str
    criterion_type: Literal[
        "eval_target", "deliverable", "test_requirement", "functional"
    ]
    description: str
    verification_method: Literal[
        "eval_score",
        "file_exists",
        "function_exists",
        "test_passes",
        "command_exits_zero",
        "grep_match",
    ]
    target: dict[str, Any]


class CriterionResult(BaseModel):
    criterion: AcceptanceCriterion
    passed: bool
    actual_value: str | None = None
    expected_value: str | None = None
    evidence: list[str] = []
    error: str | None = None


class HypothesisVerdict(BaseModel):
    hypothesis_id: str
    hypothesis_title: str
    criteria: list[CriterionResult]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.criteria)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        if not self.criteria:
            return 0.0
        return sum(1 for c in self.criteria if c.passed) / len(self.criteria)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unsatisfied(self) -> list[str]:
        return [c.criterion.description for c in self.criteria if not c.passed]


class ReportSummary(BaseModel):
    total_criteria: int
    passed_criteria: int
    failed_criteria: int
    error_criteria: int
    failed_hypotheses: list[str] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        if self.total_criteria == 0:
            return 0.0
        return self.passed_criteria / self.total_criteria

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_passed(self) -> bool:
        return self.failed_criteria == 0 and self.error_criteria == 0


class VerificationReport(BaseModel):
    hypotheses: list[HypothesisVerdict]
    summary: ReportSummary

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("## Acceptance Criteria Verification Report\n")
        s = self.summary
        lines.append(f"**Total:** {s.total_criteria} | "
                      f"**Passed:** {s.passed_criteria} | "
                      f"**Failed:** {s.failed_criteria} | "
                      f"**Errors:** {s.error_criteria} | "
                      f"**Pass rate:** {s.pass_rate:.0%}\n")

        for verdict in self.hypotheses:
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
            for verdict in self.hypotheses
            for cr in verdict.criteria
            if not cr.passed
        ]
        if unsatisfied:
            lines.append("### Unsatisfied Acceptance Criteria\n")
            for cr in unsatisfied:
                lines.append(f"- **{cr.criterion.criterion_id}**: {cr.criterion.description}")
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
