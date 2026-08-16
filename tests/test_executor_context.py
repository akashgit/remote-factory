"""Tests for WorkflowExecutor context template expansion and gate verdict parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import (
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def _make_executor(
    tmp_path: Path,
    nodes: dict | None = None,
    edges: list | None = None,
    context: dict[str, str] | None = None,
) -> WorkflowExecutor:
    """Build a minimal WorkflowExecutor for testing."""
    if nodes is None:
        nodes = {
            "start": FnNode(id="start", command="echo hello"),
        }
    if edges is None:
        edges = []
    wf = Workflow(
        name="test",
        nodes=nodes,
        edges=edges,
        start_node="start",
    )
    return WorkflowExecutor(wf, tmp_path, dry_run=True, context=context)


class TestExpandTemplates:
    """Tests for _expand_templates method."""

    def test_replaces_project_path(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        result = ex._expand_templates("cd {project_path}")
        assert str(tmp_path) in result
        assert "{project_path}" not in result

    def test_replaces_context_variables(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path, context={"container_name": "fb-test-123"})
        result = ex._expand_templates("docker exec {container_name} bash")
        assert "fb-test-123" in result
        assert "{container_name}" not in result

    def test_replaces_multiple_context_variables(self, tmp_path: Path) -> None:
        ex = _make_executor(
            tmp_path, context={"container_name": "ctr", "image": "img:latest"}
        )
        result = ex._expand_templates("{container_name} and {image}")
        assert "ctr" in result
        assert "img:latest" in result

    def test_no_context_leaves_unknown_placeholders(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        result = ex._expand_templates("{unknown_var}")
        assert "{unknown_var}" in result

    def test_empty_context(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path, context={})
        result = ex._expand_templates("{project_path}/foo")
        assert str(tmp_path) in result

    def test_quotes_values_with_spaces(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path, context={"name": "has space"})
        result = ex._expand_templates("{name}")
        assert "has space" in result


class TestParseFnVerdictReloop:
    """Tests for _parse_fn_verdict handling RELOOP outputs."""

    def test_reloop_on_pytest_failed(self, tmp_path: Path) -> None:
        """Gate outputs 'RELOOP: pytest failed' → Verdict is RELOOP."""
        gate = GateNode(id="gate", evaluator_type="fn")
        nodes = {
            "start": FnNode(id="start", command="echo hello"),
            "builder": FnNode(id="builder", command="echo build"),
            "gate": gate,
        }
        edges = [
            Edge(source="start", target="builder"),
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="builder", condition=VerdictType.RELOOP),
        ]
        ex = _make_executor(tmp_path, nodes=nodes, edges=edges)

        output = "RELOOP: pytest failed\n===== 3 failed, 2 passed =====\n"
        verdict = ex._parse_fn_verdict(output, "gate")
        assert verdict.type == VerdictType.RELOOP
        assert verdict.target == "builder"
        assert "pytest failed" in (verdict.feedback or "")

    def test_reloop_on_spec_compliance_fail(self, tmp_path: Path) -> None:
        """Gate outputs 'RELOOP: spec compliance check failed' → RELOOP."""
        gate = GateNode(id="gate", evaluator_type="fn")
        nodes = {
            "start": FnNode(id="start", command="echo hello"),
            "builder": FnNode(id="builder", command="echo build"),
            "gate": gate,
        }
        edges = [
            Edge(source="start", target="builder"),
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="builder", condition=VerdictType.RELOOP),
        ]
        ex = _make_executor(tmp_path, nodes=nodes, edges=edges)

        output = "RELOOP: spec compliance check failed\n"
        verdict = ex._parse_fn_verdict(output, "gate")
        assert verdict.type == VerdictType.RELOOP
        assert verdict.target == "builder"

    def test_proceed_on_tests_pass(self, tmp_path: Path) -> None:
        """Gate outputs 'PROCEED' → Verdict is PROCEED."""
        ex = _make_executor(tmp_path)
        output = "PROCEED\n"
        verdict = ex._parse_fn_verdict(output, "gate")
        assert verdict.type == VerdictType.PROCEED

    def test_proceed_on_spec_compliance_pass(self, tmp_path: Path) -> None:
        """Multi-line output starting with PROCEED → PROCEED."""
        ex = _make_executor(tmp_path)
        output = "PROCEED\nall checks passed\n"
        verdict = ex._parse_fn_verdict(output, "gate")
        assert verdict.type == VerdictType.PROCEED

    def test_halt_on_no_commits(self, tmp_path: Path) -> None:
        """Gate outputs 'HALT: ...' → Verdict is HALT."""
        ex = _make_executor(tmp_path)
        output = "HALT: builder did not commit any changes\n"
        verdict = ex._parse_fn_verdict(output, "gate")
        assert verdict.type == VerdictType.HALT


class TestExecutorContextInit:
    """Tests for context parameter initialization."""

    def test_default_context_is_empty(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path)
        assert ex.context == {}

    def test_context_stored(self, tmp_path: Path) -> None:
        ctx = {"container_name": "test-ctr", "foo": "bar"}
        ex = _make_executor(tmp_path, context=ctx)
        assert ex.context == ctx

    def test_none_context_becomes_empty_dict(self, tmp_path: Path) -> None:
        ex = _make_executor(tmp_path, context=None)
        assert ex.context == {}


class TestHostGateIntegration:
    """Integration tests for the host-side featurebench gate (gate_tests)."""

    def test_host_gate_has_container_name_placeholder(self) -> None:
        """gate_tests evaluator_command uses {container_name}."""
        import sys
        sys.path.insert(0, str(Path(__file__).parents[3]))
        # Read the featurebench.py workflow file to check its structure
        fb_path = Path(__file__).parents[3] / ".factory" / "workflows" / "featurebench.py"
        if not fb_path.exists():
            pytest.skip("Host featurebench workflow not found")
        content = fb_path.read_text()
        assert "{container_name}" in content
        assert "docker exec" in content

    def test_host_gate_no_resolved_grep(self) -> None:
        """Host gate does NOT grep for RESOLVED."""
        fb_path = Path(__file__).parents[3] / ".factory" / "workflows" / "featurebench.py"
        if not fb_path.exists():
            pytest.skip("Host featurebench workflow not found")
        content = fb_path.read_text()
        # The gate_tests evaluator_command should not contain 'RESOLVED'
        # (only the L2 fallback greps for SPEC_COMPLIANCE)
        lines = content.split("\n")
        in_gate = False
        gate_lines = []
        for line in lines:
            if "gate_tests" in line and "GateNode" in line:
                in_gate = True
            elif in_gate:
                gate_lines.append(line)
                if line.strip().startswith(")") and not line.strip().startswith("'"):
                    break
        gate_text = "\n".join(gate_lines)
        assert "RESOLVED" not in gate_text

    def test_host_gate_spec_compliance_l2_path(self) -> None:
        """Host gate checks SPEC_COMPLIANCE for L2 (no test files)."""
        fb_path = Path(__file__).parents[3] / ".factory" / "workflows" / "featurebench.py"
        if not fb_path.exists():
            pytest.skip("Host featurebench workflow not found")
        content = fb_path.read_text()
        assert "SPEC_COMPLIANCE: PASS" in content

    def test_host_health_checker_no_resolved(self) -> None:
        """Health checker prompt does NOT instruct writing RESOLVED."""
        fb_path = Path(__file__).parents[3] / ".factory" / "workflows" / "featurebench.py"
        if not fb_path.exists():
            pytest.skip("Host featurebench workflow not found")
        content = fb_path.read_text()
        # Find the health_checker prompt_template
        # It should contain "DO NOT write RESOLVED"
        assert "DO NOT write RESOLVED" in content
