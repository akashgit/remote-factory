"""Tests for factory/workflow/splitter.py — template resolver + annotation extractor."""

import yaml

from factory.workflow.splitter import (
    annotations_to_yaml,
    extract_annotations,
    resolve_to_clean,
    split_skill,
)


SAMPLE_TEMPLATIZED = """\
## Phase 5: QA Verification

<!-- node: AgentNode id=qa role=QA blocking=true -->
<!-- reads: .factory/reviews/builder-latest.md -->
<!-- writes: .factory/reviews/qa-latest.md -->
<!-- edges: unconditional → gate_qa -->

```bash
factory agent qa --task "{{task_prompt_qa::Run health check.}}" --project "$PROJECT_PATH" --timeout {{timeout_qa::600}}
```

<!-- gate: GateNode id=gate_qa evaluator_type=agent evaluator_role=CEO -->
<!-- reads: .factory/reviews/qa-latest.md -->
<!-- edges: PROCEED → gate_precheck, RELOOP → builder -->

### CEO Review — QA

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/qa-latest.md`
3. Assess: {{gate_prompt_gate_qa::Review QA results.}}
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`

*On RELOOP: return to `builder` (max {{max_iterations_gate_qa::3}} iterations)*

<!-- gate: GateNode id=gate_precheck evaluator_type=fn -->
<!-- evaluator_command: factory precheck {project_path} -->
<!-- reads: .factory/reviews/qa-latest.md -->
<!-- edges: PROCEED → finalize -->

### Gate — Precheck (Automated)

```bash
factory precheck $PROJECT_PATH
```

{{failure_action_gate_precheck::}}
"""


class TestResolveToClean:
    def test_strips_annotations(self) -> None:
        result = resolve_to_clean(SAMPLE_TEMPLATIZED)
        assert "<!--" not in result
        assert "-->" not in result

    def test_resolves_slots(self) -> None:
        result = resolve_to_clean(SAMPLE_TEMPLATIZED)
        assert "{{" not in result
        assert "}}" not in result
        assert "Run health check." in result
        assert "--timeout 600" in result

    def test_preserves_prose(self) -> None:
        result = resolve_to_clean(SAMPLE_TEMPLATIZED)
        assert "## Phase 5: QA Verification" in result
        assert "CEO Review — QA" in result
        assert "Gate — Precheck (Automated)" in result

    def test_no_triple_newlines(self) -> None:
        result = resolve_to_clean(SAMPLE_TEMPLATIZED)
        assert "\n\n\n" not in result


class TestExtractAnnotations:
    def test_extracts_agent_node(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "qa" in annotations
        assert annotations["qa"]["type"] == "AgentNode"
        assert annotations["qa"]["role"] == "QA"

    def test_extracts_gate_node(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "gate_qa" in annotations
        assert annotations["gate_qa"]["type"] == "GateNode"
        assert annotations["gate_qa"]["evaluator_type"] == "agent"

    def test_extracts_fn_gate(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "gate_precheck" in annotations
        assert annotations["gate_precheck"]["evaluator_type"] == "fn"

    def test_extracts_reads_writes(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert ".factory/reviews/builder-latest.md" in annotations["qa"]["reads"]
        assert ".factory/reviews/qa-latest.md" in annotations["qa"]["writes"]

    def test_extracts_edges(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        qa_edges = annotations["qa"]["edges_out"]
        assert len(qa_edges) == 1
        assert qa_edges[0]["target"] == "gate_qa"
        assert qa_edges[0]["condition"] is None

    def test_extracts_conditional_edges(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        gate_edges = annotations["gate_qa"]["edges_out"]
        targets = {e["target"] for e in gate_edges}
        assert "gate_precheck" in targets
        assert "builder" in targets

    def test_extracts_evaluator_command(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "evaluator_command" in annotations["gate_precheck"]


class TestSplitSkill:
    def test_returns_clean_and_annotations(self) -> None:
        clean, annotations = split_skill(SAMPLE_TEMPLATIZED)
        assert isinstance(clean, str)
        assert isinstance(annotations, dict)

    def test_clean_has_no_markers(self) -> None:
        clean, _ = split_skill(SAMPLE_TEMPLATIZED)
        assert "{{" not in clean
        assert "<!--" not in clean

    def test_annotations_have_slots(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        assert "slots" in annotations["qa"]
        assert "task_prompt_qa" in annotations["qa"]["slots"]
        assert "timeout_qa" in annotations["qa"]["slots"]

    def test_gate_annotations_have_slots(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        assert "slots" in annotations["gate_qa"]
        assert "gate_prompt_gate_qa" in annotations["gate_qa"]["slots"]


class TestAnnotationsToYaml:
    def test_produces_valid_yaml(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        yaml_str = annotations_to_yaml(annotations)
        parsed = yaml.safe_load(yaml_str)
        assert isinstance(parsed, dict)
        assert "qa" in parsed

    def test_roundtrip(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        yaml_str = annotations_to_yaml(annotations)
        parsed = yaml.safe_load(yaml_str)
        assert parsed["qa"]["type"] == "AgentNode"
        assert parsed["qa"]["role"] == "QA"


class TestRoundTrip:
    def test_templatize_then_split_preserves_content(self) -> None:
        """Verify that templatizing then splitting produces clean output
        with the same prose content (minus markers and annotations)."""
        from factory.workflow.definitions import improve_workflow
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = improve_workflow()
        templatized = workflow_to_skill_md(wf)
        clean, annotations = split_skill(templatized)

        assert "{{" not in clean
        assert "<!--" not in clean
        assert "factory agent builder" in clean
        assert "factory agent qa" in clean

        assert "builder" in annotations or "qa" in annotations
