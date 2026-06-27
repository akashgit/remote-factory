"""Tests for factory/workflow/context.py — DAG context derivation."""

from factory.workflow.context import (
    derive_context,
    format_context_for_agent,
)


class TestDeriveContext:
    def test_returns_all_sections(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        assert "agent_prompts" in ctx
        assert "commands" in ctx
        assert "edge_topology" in ctx
        assert "node_summary" in ctx

    def test_extracts_agent_prompts(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        prompts = ctx["agent_prompts"]
        assert "researcher" in prompts
        assert "builder" in prompts
        assert "qa" in prompts

    def test_extracts_ceo_prompt_from_gates(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        assert "ceo" in ctx["agent_prompts"]

    def test_extracts_fn_commands(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        assert "begin" in ctx["commands"]
        assert "finalize" in ctx["commands"]

    def test_extracts_gate_evaluator_commands(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        assert "gate_precheck" in ctx["commands"]

    def test_extracts_edge_topology(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        edges = ctx["edge_topology"]
        assert len(edges) > 0
        sources = {e["source"] for e in edges}
        assert "builder" in sources

    def test_extracts_node_summary(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        summary = ctx["node_summary"]
        assert "builder" in summary
        assert summary["builder"]["type"] == "AgentNode"
        assert summary["builder"]["role"] == "builder"

    def test_gate_summary_has_evaluator_type(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        assert ctx["node_summary"]["gate_precheck"]["evaluator_type"] == "fn"

    def test_works_with_fork_join_workflow(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        ctx = derive_context(wf)
        assert len(ctx["agent_prompts"]) > 0
        assert len(ctx["edge_topology"]) > 0


class TestFormatContextForAgent:
    def test_produces_text(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        text = format_context_for_agent(ctx)
        assert isinstance(text, str)
        assert "## Agent Prompts" in text
        assert "## CLI Commands" in text
        assert "## Edge Topology" in text
        assert "## Node Summary" in text

    def test_includes_role_names(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        ctx = derive_context(wf)
        text = format_context_for_agent(ctx)
        assert "builder" in text
        assert "qa" in text
