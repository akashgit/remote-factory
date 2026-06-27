"""Tests for factory/workflow/templates.py — template slot format parser."""

from factory.workflow.templates import emit, extract, resolve


class TestEmit:
    def test_basic_slot(self) -> None:
        result = emit("timeout_qa", "1800")
        assert result == "{{timeout_qa::1800}}"

    def test_slot_with_long_value(self) -> None:
        result = emit("task_prompt_builder", "Build the thing and test it.")
        assert result == "{{task_prompt_builder::Build the thing and test it.}}"

    def test_empty_value(self) -> None:
        result = emit("failure_action_precheck", "")
        assert result == "{{failure_action_precheck::}}"


class TestResolve:
    def test_strips_markers(self) -> None:
        text = "timeout {{timeout_qa::1800}} seconds"
        assert resolve(text) == "timeout 1800 seconds"

    def test_multiple_slots(self) -> None:
        text = "{{slot_a::alpha}} and {{slot_b::beta}}"
        assert resolve(text) == "alpha and beta"

    def test_no_slots(self) -> None:
        text = "plain text with no markers"
        assert resolve(text) == text

    def test_empty_value(self) -> None:
        text = "before {{empty_slot::}} after"
        assert resolve(text) == "before  after"

    def test_multiline_value(self) -> None:
        text = "cmd --task \"{{task::line1\nline2}}\""
        assert resolve(text) == 'cmd --task "line1\nline2"'

    def test_preserves_surrounding_text(self) -> None:
        text = "```bash\nfactory agent qa --timeout {{timeout_qa::600}}\n```"
        assert resolve(text) == "```bash\nfactory agent qa --timeout 600\n```"


class TestExtract:
    def test_basic_extraction(self) -> None:
        text = "{{timeout_qa::1800}}"
        result = extract(text)
        assert result == [("timeout_qa", "1800")]

    def test_multiple_slots(self) -> None:
        text = "{{slot_a::alpha}} and {{slot_b::beta}}"
        result = extract(text)
        assert result == [("slot_a", "alpha"), ("slot_b", "beta")]

    def test_no_slots(self) -> None:
        assert extract("plain text") == []

    def test_empty_value(self) -> None:
        result = extract("{{empty::}}")
        assert result == [("empty", "")]

    def test_slot_names_preserved(self) -> None:
        text = "{{task_prompt_qa::Run checks}} then {{timeout_qa::600}}"
        result = extract(text)
        names = [name for name, _ in result]
        assert "task_prompt_qa" in names
        assert "timeout_qa" in names


class TestRoundTrip:
    def test_emit_then_resolve(self) -> None:
        slot = emit("timeout_qa", "1800")
        text = f"factory agent qa --timeout {slot}"
        resolved = resolve(text)
        assert resolved == "factory agent qa --timeout 1800"

    def test_emit_then_extract(self) -> None:
        slot = emit("gate_prompt_qa", "Check quality.")
        text = f"Assess: {slot}"
        result = extract(text)
        assert result == [("gate_prompt_qa", "Check quality.")]
