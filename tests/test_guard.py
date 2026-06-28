"""Tests for factory/workflow/guard.py — structural diff checker."""

from factory.workflow.guard import GuardResult, check


class TestGuardProceed:
    def test_identical_input(self) -> None:
        text = "Some text {{slot_a::value}} more text"
        result = check(text, text)
        assert result.passed
        assert result.verdict == "PROCEED"
        assert result.violations == []

    def test_only_slot_values_differ(self) -> None:
        skeleton = "cmd --timeout {{timeout_qa::600}} --task {{task_qa::do stuff}}"
        refined = "cmd --timeout {{timeout_qa::1800}} --task {{task_qa::do better stuff}}"
        result = check(skeleton, refined)
        assert result.passed
        assert result.verdict == "PROCEED"

    def test_annotations_unchanged_slots_changed(self) -> None:
        skeleton = (
            "<!-- node: AgentNode id=qa -->\n"
            "```bash\nfactory agent qa --timeout {{timeout_qa::600}}\n```"
        )
        refined = (
            "<!-- node: AgentNode id=qa -->\n"
            "```bash\nfactory agent qa --timeout {{timeout_qa::1800}}\n```"
        )
        result = check(skeleton, refined)
        assert result.passed

    def test_empty_slot_value_changed_to_content(self) -> None:
        skeleton = "{{failure_action::}}"
        refined = "{{failure_action::If fails, revert.}}"
        result = check(skeleton, refined)
        assert result.passed


class TestGuardReloop:
    def test_text_outside_slots_changed(self) -> None:
        skeleton = "Run this command {{slot::val}}"
        refined = "Execute this command {{slot::val}}"
        result = check(skeleton, refined)
        assert not result.passed
        assert result.verdict == "RELOOP"
        assert any("Text outside" in v for v in result.violations)

    def test_slot_added(self) -> None:
        skeleton = "{{slot_a::val}}"
        refined = "{{slot_a::val}} {{slot_b::extra}}"
        result = check(skeleton, refined)
        assert not result.passed
        assert any("added" in v.lower() for v in result.violations)

    def test_slot_removed(self) -> None:
        skeleton = "{{slot_a::val}} {{slot_b::val2}}"
        refined = "{{slot_a::val}}"
        result = check(skeleton, refined)
        assert not result.passed
        assert any("removed" in v.lower() for v in result.violations)

    def test_annotation_comment_modified(self) -> None:
        skeleton = "<!-- node: AgentNode id=qa -->\ntext {{slot::val}}"
        refined = "<!-- node: AgentNode id=qa_changed -->\ntext {{slot::val}}"
        result = check(skeleton, refined)
        assert not result.passed
        assert any("Annotation" in v for v in result.violations)

    def test_annotation_removed(self) -> None:
        skeleton = "<!-- comment -->\n{{slot::val}}"
        refined = "{{slot::val}}"
        result = check(skeleton, refined)
        assert not result.passed

    def test_annotation_added(self) -> None:
        skeleton = "{{slot::val}}"
        refined = "<!-- new comment -->\n{{slot::val}}"
        result = check(skeleton, refined)
        assert not result.passed

    def test_multiple_violations(self) -> None:
        skeleton = "text {{slot_a::val}}"
        refined = "changed {{slot_b::val}}"
        result = check(skeleton, refined)
        assert not result.passed
        assert len(result.violations) >= 2


class TestGuardResult:
    def test_passed_property(self) -> None:
        assert GuardResult(verdict="PROCEED").passed
        assert not GuardResult(verdict="RELOOP").passed
        assert not GuardResult(verdict="RELOOP", violations=["issue"]).passed
