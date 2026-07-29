"""W4: Research Mode workflow definition."""

from __future__ import annotations

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions._shared import _deep_qa_subgraph
from factory.workflow.definitions.improve import improve_workflow
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)


def research_workflow() -> Workflow:
    """W4: Research Mode — extends W3 with baseline measurement, failure analyst,
    research command eval, and plateau detection.

    W4 = W3[study <- (baseline -> failure_analyst -> researcher),
             qa <- QA with surface constraint verification, + plateau_gate]
    """
    wf = improve_workflow()

    # Replace study with baseline measurement
    del wf.nodes["study"]

    wf.nodes["baseline"] = FnNode(
        id="baseline",
        command="factory eval {project_path}",
        notes="Run baseline evaluation to capture current scores before any changes. Must run before failure analysis.",
        writes={".factory/experiments/baseline.json"},
    )

    # Insert failure analyst
    wf.nodes["failure_analyst"] = AgentNode(
        id="failure_analyst",
        role=AgentRole.FAILURE_ANALYST,
        prompt_template=(
            "Analyze research run results. "
            "Read run artifacts at .factory/research/runs/. "
            "Read research target config from .factory/config.json. "
            "Classify failures by type and severity. "
            "Compute failure distribution. "
            "Suggest interventions within mutable surfaces only. "
            "Write to .factory/strategy/failure_analysis.md."
        ),
        reads={".factory/experiments/baseline.json"},
        writes={".factory/strategy/failure_analysis.md"},
    )

    # Update researcher to read failure analysis
    wf.nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Failure-targeted research. "
            "Read failure analysis at .factory/strategy/failure_analysis.md. "
            "Search the web for solutions to the dominant failure modes. "
            "Check .factory/archive/ for prior knowledge on these patterns. "
            "Write findings to .factory/strategy/research-local.md."
        ),
        reads={".factory/strategy/failure_analysis.md"},
        writes={".factory/strategy/research-local.md"},
    )

    # Update strategist to read failure analysis instead of observations
    wf.nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Generate research hypotheses targeting dominant failure modes. "
            "Each hypothesis must improve over the previous baseline score. "
            "Each hypothesis must name specific files from mutable_surfaces to modify. "
            "Hypotheses MUST NOT modify files in fixed_surfaces. "
            "Prioritize by expected impact on the target metric. "
            "Write 1-3 hypotheses to .factory/strategy/current.md."
        ),
        reads={".factory/strategy/research-local.md", ".factory/strategy/failure_analysis.md"},
        writes={".factory/strategy/current.md"},
    )

    # Override deep-qa subgraph with research-specific code reviewer extra
    dq_nodes, dq_edges = _deep_qa_subgraph(
        code_reviewer_extra=(
            "Verify mutable/fixed surface constraint compliance. "
            "Check that no files in fixed_surfaces were modified."
        ),
    )
    wf.nodes.update(dq_nodes)

    # Add plateau gate after finalize — checks if score improved over prior runs
    wf.nodes["plateau_gate"] = GateNode(
        id="plateau_gate",
        evaluator_type="fn",
        evaluator_command=(
            'python3 -c "'
            "import json, pathlib, sys; "
            "tsv = pathlib.Path('{project_path}/.factory/results.tsv'); "
            "lines = [l for l in tsv.read_text().strip().splitlines()[1:] if l.strip()] if tsv.exists() else []; "
            "scores = []; "
            "[scores.append(float(p)) for l in lines for i, p in enumerate(l.split(chr(9))) if i == 2 and p]; "
            "recent = scores[-3:] if len(scores) >= 3 else scores; "
            "improved = len(recent) < 2 or recent[-1] > recent[-2]; "
            "print('RELOOP' if improved else 'PROCEED')"
            '"'
        ),
        reads={".factory/experiments/verdict.json"},
    )

    # Rebuild edges for research flow
    wf.edges = [
        # Baseline -> failure analyst -> researcher
        Edge(source="baseline", target="failure_analyst"),
        Edge(source="failure_analyst", target="researcher"),
        # Researcher -> research gate
        Edge(source="researcher", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="researcher", condition=VerdictType.RELOOP),
        # Strategist -> strategy gate -> apply spec diff -> begin
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="apply_spec_diff", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # apply_spec_diff -> begin
        Edge(source="apply_spec_diff", target="begin"),
        # begin -> builder
        Edge(source="begin", target="builder"),
        # Builder -> build gate
        Edge(source="builder", target="gate_build"),
        # Build gate -> deep-qa (proceed) or builder (reloop)
        Edge(source="gate_build", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Deep-QA internal edges
        *dq_edges,
        # adversarial_tester -> gate_qa
        Edge(source="adversarial_tester", target="gate_qa"),
        # gate_qa -> doc freshness (proceed) or builder (reloop, max 3)
        Edge(source="gate_qa", target="gate_doc_freshness", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        # Doc freshness -> precheck (proceed) or builder (reloop)
        Edge(source="gate_doc_freshness", target="gate_precheck", condition=VerdictType.PROCEED),
        Edge(source="gate_doc_freshness", target="builder", condition=VerdictType.RELOOP),
        Edge(source="gate_precheck", target="finalize", condition=VerdictType.PROCEED),
        Edge(source="gate_precheck", target="archivist", condition=VerdictType.HALT),
        # Finalize -> archivist -> spec_update (non-blocking) -> plateau gate
        Edge(source="finalize", target="archivist"),
        Edge(source="archivist", target="spec_update"),
        Edge(source="spec_update", target="plateau_gate"),
        # Plateau gate: proceed (done) or reloop to baseline
        Edge(source="plateau_gate", target="baseline", condition=VerdictType.RELOOP),
    ]

    wf.name = "research"
    wf.start_node = "baseline"

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and bool(ctx.get("research_target"))

    wf.trigger = trigger
    return wf
