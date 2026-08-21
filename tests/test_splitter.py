"""Tests for factory/workflow/splitter.py — template resolver + annotation extractor."""

import yaml

from factory.workflow.splitter import (
    annotations_to_yaml,
    extract_annotations,
    resolve_to_clean,
    split_skill,
)


SAMPLE_TEMPLATIZED = """\
## Phase 5: Health Check

<!-- node: AgentNode id=health_checker role=HEALTH_CHECKER blocking=true -->
<!-- reads: .factory/reviews/builder-latest.md -->
<!-- writes: .factory/reviews/health-check.md -->
<!-- edges: unconditional → gate_health_checker -->

```bash
factory agent health_checker --task "{{task_prompt_health_checker::Run health check.}}" --project "$PROJECT_PATH" --timeout {{timeout_health_checker::600}}
```

<!-- gate: GateNode id=gate_health_checker evaluator_type=agent evaluator_role=CEO -->
<!-- reads: .factory/reviews/health-check.md -->
<!-- edges: PROCEED → gate_precheck, RELOOP → builder -->

### CEO Review — QA

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/health-check.md`
3. Assess: {{gate_prompt_gate_health_checker::Review QA results.}}
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`

*On RELOOP: return to `builder` (max {{max_iterations_gate_health_checker::3}} iterations)*

<!-- gate: GateNode id=gate_precheck evaluator_type=fn -->
<!-- evaluator_command: factory precheck {project_path} -->
<!-- reads: .factory/reviews/health-check.md -->
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
        assert "## Phase 5: Health Check" in result
        assert "CEO Review — QA" in result
        assert "Gate — Precheck (Automated)" in result

    def test_no_triple_newlines(self) -> None:
        result = resolve_to_clean(SAMPLE_TEMPLATIZED)
        assert "\n\n\n" not in result


class TestExtractAnnotations:
    def test_extracts_agent_node(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "health_checker" in annotations
        assert annotations["health_checker"]["type"] == "AgentNode"
        assert annotations["health_checker"]["role"] == "HEALTH_CHECKER"

    def test_extracts_gate_node(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "gate_health_checker" in annotations
        assert annotations["gate_health_checker"]["type"] == "GateNode"
        assert annotations["gate_health_checker"]["evaluator_type"] == "agent"

    def test_extracts_fn_gate(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert "gate_precheck" in annotations
        assert annotations["gate_precheck"]["evaluator_type"] == "fn"

    def test_extracts_reads_writes(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        assert ".factory/reviews/builder-latest.md" in annotations["health_checker"]["reads"]
        assert ".factory/reviews/health-check.md" in annotations["health_checker"]["writes"]

    def test_extracts_edges(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        hc_edges = annotations["health_checker"]["edges_out"]
        assert len(hc_edges) == 1
        assert hc_edges[0]["target"] == "gate_health_checker"
        assert hc_edges[0]["condition"] is None

    def test_extracts_conditional_edges(self) -> None:
        annotations = extract_annotations(SAMPLE_TEMPLATIZED)
        gate_edges = annotations["gate_health_checker"]["edges_out"]
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
        assert "slots" in annotations["health_checker"]
        assert "task_prompt_health_checker" in annotations["health_checker"]["slots"]
        assert "timeout_health_checker" in annotations["health_checker"]["slots"]

    def test_gate_annotations_have_slots(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        assert "slots" in annotations["gate_health_checker"]
        assert "gate_prompt_gate_health_checker" in annotations["gate_health_checker"]["slots"]


class TestAnnotationsToYaml:
    def test_produces_valid_yaml(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        yaml_str = annotations_to_yaml(annotations)
        parsed = yaml.safe_load(yaml_str)
        assert isinstance(parsed, dict)
        assert "health_checker" in parsed

    def test_roundtrip(self) -> None:
        _, annotations = split_skill(SAMPLE_TEMPLATIZED)
        yaml_str = annotations_to_yaml(annotations)
        parsed = yaml.safe_load(yaml_str)
        assert parsed["health_checker"]["type"] == "AgentNode"
        assert parsed["health_checker"]["role"] == "HEALTH_CHECKER"


class TestRoundTrip:
    def test_templatize_then_split_preserves_content(self) -> None:
        """Verify that templatizing then splitting produces clean output
        with the same prose content (minus markers and annotations)."""
        import re as _re

        from factory.workflow.definitions import build_workflow
        from factory.workflow.skill_export import workflow_to_skill_md

        wf = build_workflow()
        templatized = workflow_to_skill_md(wf)
        clean, annotations = split_skill(templatized)

        assert not _re.search(r"\{\{[a-z_]\w*::", clean), "unresolved template slots in clean output"
        assert "<!--" not in clean
        assert "factory agent builder" in clean
        assert "factory agent health_checker" in clean

        assert "builder" in annotations or "health_checker" in annotations
