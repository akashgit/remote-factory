"""Tests for the mini-swebench contributed workflow."""

from factory.workflow.contributed.mini_swebench.workflow import workflow
from factory.workflow.primitives import FnNode, GateNode, LLMNode


def test_workflow_structure():
    wf = workflow()
    assert wf.name == "mini-swebench"
    assert len(wf.nodes) == 4
    assert wf.start_node == "read_task"
    assert wf.terminal is True


def test_node_types():
    wf = workflow()
    assert isinstance(wf.nodes["read_task"], FnNode)
    assert isinstance(wf.nodes["solver"], LLMNode)
    assert isinstance(wf.nodes["gate_verify"], GateNode)
    assert isinstance(wf.nodes["auto_merge"], FnNode)


def test_solver_has_bash_tool():
    wf = workflow()
    solver = wf.nodes["solver"]
    assert isinstance(solver, LLMNode)
    assert len(solver.tools) == 1
    assert solver.tools[0].name == "bash"
    assert solver.tools[0].executor == "bash"


def test_solver_prompt_content():
    wf = workflow()
    solver = wf.nodes["solver"]
    assert isinstance(solver, LLMNode)
    assert "programming tasks" in solver.system_prompt
    assert "<instructions>" in solver.instance_prompt
    assert "{instance_context}" in solver.instance_prompt


def test_edges():
    wf = workflow()
    edges = {(e.source, e.target): e.condition for e in wf.edges}
    assert ("read_task", "solver") in edges
    assert ("solver", "gate_verify") in edges
    assert edges[("read_task", "solver")] is None
