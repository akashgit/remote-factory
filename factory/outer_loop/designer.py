"""Designer Agent — dual-mode workflow designer and informed mutation proposer.

Design mode: creates from-scratch workflow designs (minimal, thorough, custom).
Mutation mode: proposes targeted mutations based on failure telemetry.

Prompts are populated with benchmark-specific context so all designed workflows
produce agents that reference task files, testbed paths, and test files.
"""

from __future__ import annotations

import structlog

from factory.outer_loop.models import EvalResult, MutationRecord, MutationType
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    Workflow,
)

log = structlog.get_logger()

_ROLE_PROMPT_TEMPLATES: dict[str, str] = {
    "researcher": (
        "Study the codebase at {{testbed_path}}. Read the issue at {{task_file}}. "
        "Explore the repository structure, identify relevant files, and write "
        "findings to .factory/strategy/research.md."
    ),
    "builder": (
        "Read the task description at {{task_file}}. Read any prior research at "
        ".factory/strategy/research.md. Implement the fix in the codebase at "
        "{{testbed_path}}. Run pytest on {{test_file}} to verify. Fix any failures. "
        "Commit your changes."
    ),
    "health_checker": (
        "Run the test suite at {{test_file}} in the codebase at {{testbed_path}}. "
        "Report results including pass/fail counts."
    ),
    "code_reviewer": (
        "Review the changes made in {{testbed_path}} for the task described in "
        "{{task_file}}. Check for correctness, edge cases, and style."
    ),
    "adversarial_tester": (
        "Test the implementation in {{testbed_path}} adversarially. Try edge cases "
        "and unexpected inputs. Run {{test_file}} and report any failures."
    ),
    "strategist": (
        "Read the research at .factory/strategy/research.md and the task at "
        "{{task_file}}. Formulate a strategy for solving the issue."
    ),
}


def populate_prompt(role: str, benchmark_spec: str) -> str:
    """Generate a role-specific functional prompt with benchmark context.

    The benchmark_spec is used as-is for placeholder values when specific
    paths are not known at design time.
    """
    template = _ROLE_PROMPT_TEMPLATES.get(role)
    if template is None:
        return f"Complete the {role} task for the benchmark: {benchmark_spec}"

    return (
        template
        .replace("{{testbed_path}}", "/tmp/testbed")
        .replace("{{task_file}}", "/tmp/testbed/task-instruction.md")
        .replace("{{test_file}}", "the relevant test files")
    )


class DesignerAgent:
    """LLM-guided workflow designer with design and mutation modes.

    Design mode produces from-scratch workflows for seed diversity.
    Mutation mode proposes targeted mutations from failure telemetry.
    All designed workflows include benchmark-specific prompts.
    """

    def design_minimal(self, benchmark_spec: str) -> Workflow:
        """Create a 3-4 node workflow optimized for speed.

        Structure: researcher → builder → gate
        """
        nodes: dict[str, AgentNode | FnNode | GateNode] = {
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                prompt_template=populate_prompt("researcher", benchmark_spec),
                writes={".factory/strategy/research.md"},
                timeout=300,
            ),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template=populate_prompt("builder", benchmark_spec),
                reads={".factory/strategy/research.md"},
                writes={".factory/reviews/builder-latest.md"},
                timeout=600,
            ),
            "gate_qa": GateNode(
                id="gate_qa",
                evaluator_type="agent",
                evaluator_role=AgentRole.HEALTH_CHECKER,
                reads={".factory/reviews/builder-latest.md"},
            ),
        }
        edges = [
            Edge(source="researcher", target="builder"),
            Edge(source="builder", target="gate_qa"),
        ]
        wf = Workflow(
            name=f"minimal_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node="researcher",
        )
        log.info("designed_minimal", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def design_thorough(self, benchmark_spec: str) -> Workflow:
        """Create an 8-10 node workflow optimized for thoroughness.

        Structure: study → researcher → strategist → fork(builder_a, builder_b)
                   → join → code_reviewer → adversarial_tester → gate
        """
        from factory.workflow.primitives import ForkNode, JoinNode

        nodes: dict[str, AgentNode | FnNode | GateNode | ForkNode | JoinNode] = {
            "study": FnNode(
                id="study",
                command="factory study {project_path}",
                writes={".factory/strategy/observations.md"},
            ),
            "researcher": AgentNode(
                id="researcher",
                role=AgentRole.RESEARCHER,
                prompt_template=populate_prompt("researcher", benchmark_spec),
                reads={".factory/strategy/observations.md"},
                writes={".factory/strategy/research.md"},
                timeout=600,
            ),
            "strategist": AgentNode(
                id="strategist",
                role=AgentRole.STRATEGIST,
                prompt_template=populate_prompt("strategist", benchmark_spec),
                reads={".factory/strategy/research.md"},
                writes={".factory/strategy/current.md"},
                timeout=600,
            ),
            "fork_builders": ForkNode(
                id="fork_builders",
                targets=["builder_a", "builder_b"],
                reads={".factory/strategy/current.md"},
            ),
            "builder_a": AgentNode(
                id="builder_a",
                role=AgentRole.BUILDER,
                prompt_template=populate_prompt("builder", benchmark_spec),
                reads={".factory/strategy/current.md"},
                writes={".factory/reviews/builder-a.md"},
                timeout=1200,
            ),
            "builder_b": AgentNode(
                id="builder_b",
                role=AgentRole.BUILDER,
                prompt_template=populate_prompt("builder", benchmark_spec),
                reads={".factory/strategy/current.md"},
                writes={".factory/reviews/builder-b.md"},
                timeout=1200,
            ),
            "join_builders": JoinNode(
                id="join_builders",
                sources=["builder_a", "builder_b"],
            ),
            "code_reviewer": AgentNode(
                id="code_reviewer",
                role=AgentRole.CODE_REVIEWER,
                prompt_template=populate_prompt("code_reviewer", benchmark_spec),
                reads={".factory/reviews/builder-a.md", ".factory/reviews/builder-b.md"},
                writes={".factory/reviews/code-review.md"},
                timeout=900,
            ),
            "adversarial_tester": AgentNode(
                id="adversarial_tester",
                role=AgentRole.ADVERSARIAL_TESTER,
                prompt_template=populate_prompt("adversarial_tester", benchmark_spec),
                reads={".factory/reviews/code-review.md"},
                writes={".factory/reviews/adversarial-qa.md"},
                timeout=1800,
            ),
            "gate_qa": GateNode(
                id="gate_qa",
                evaluator_type="agent",
                evaluator_role=AgentRole.CEO,
                reads={".factory/reviews/adversarial-qa.md"},
            ),
        }
        edges = [
            Edge(source="study", target="researcher"),
            Edge(source="researcher", target="strategist"),
            Edge(source="strategist", target="fork_builders"),
            Edge(source="fork_builders", target="builder_a"),
            Edge(source="fork_builders", target="builder_b"),
            Edge(source="builder_a", target="join_builders"),
            Edge(source="builder_b", target="join_builders"),
            Edge(source="join_builders", target="code_reviewer"),
            Edge(source="code_reviewer", target="adversarial_tester"),
            Edge(source="adversarial_tester", target="gate_qa"),
        ]
        wf = Workflow(
            name=f"thorough_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node="study",
        )
        log.info("designed_thorough", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def design_custom(self, benchmark_spec: str, constraints: dict[str, object]) -> Workflow:
        """Create a custom from-scratch workflow with optional constraints.

        Constraints can specify:
        - max_nodes: int — cap on node count
        - require_roles: list[str] — roles that must be present
        - parallel: bool — whether to include fork/join parallelism
        """
        raw_max = constraints.get("max_nodes", 6)
        max_nodes = int(raw_max) if isinstance(raw_max, (int, float, str)) else 6
        raw_roles = constraints.get("require_roles", [])
        require_roles: list[object] = list(raw_roles) if isinstance(raw_roles, list) else []

        nodes: dict[str, AgentNode | FnNode | GateNode] = {}
        edges: list[Edge] = []
        prev_id: str | None = None

        core_roles: list[tuple[str, AgentRole]] = [
            ("researcher", AgentRole.RESEARCHER),
            ("strategist", AgentRole.STRATEGIST),
            ("builder", AgentRole.BUILDER),
        ]

        for role_str in require_roles:
            if isinstance(role_str, str) and not any(r[0] == role_str for r in core_roles):
                try:
                    role_enum = AgentRole(role_str)
                    core_roles.append((role_str, role_enum))
                except ValueError:
                    pass

        node_budget = max_nodes - 1
        for node_id, role in core_roles:
            if len(nodes) >= node_budget:
                break
            nodes[node_id] = AgentNode(
                id=node_id,
                role=role,
                prompt_template=populate_prompt(node_id, benchmark_spec),
                timeout=600,
            )
            if prev_id is not None:
                edges.append(Edge(source=prev_id, target=node_id))
            prev_id = node_id

        if prev_id is not None:
            gate_id = "gate_qa"
            nodes[gate_id] = GateNode(  # type: ignore[assignment]
                id=gate_id,
                evaluator_type="agent",
                evaluator_role=AgentRole.HEALTH_CHECKER,
            )
            edges.append(Edge(source=prev_id, target=gate_id))

        start = core_roles[0][0] if core_roles else "gate_qa"
        wf = Workflow(
            name=f"custom_{_slug(benchmark_spec)}",
            nodes=nodes,  # type: ignore[arg-type]
            edges=edges,
            start_node=start,
        )
        log.info("designed_custom", nodes=len(wf.nodes), benchmark=benchmark_spec[:40])
        return wf

    def propose(
        self,
        parent_workflow: Workflow,
        telemetry: dict[str, object],
        archive_stats: dict[str, object],
        benchmark_spec: str,
    ) -> list[MutationRecord]:
        """Propose 1-3 targeted mutations based on failure telemetry.

        Heuristics:
        - High failure rate on a node → propose removing or replacing it
        - Dominant failure is timeout → propose reducing parallelism or increasing timeout
        - Low diversity → propose inserting a new agent role not yet present
        """
        proposals: list[MutationRecord] = []

        node_stats = telemetry.get("node_stats", {})
        if isinstance(node_stats, dict):
            for node_id, stats in node_stats.items():
                if not isinstance(stats, dict):
                    continue
                failure_rate = stats.get("failure_rate", 0.0)
                if isinstance(failure_rate, (int, float)) and failure_rate > 0.5:
                    proposals.append(MutationRecord(
                        operator=MutationType.NODE_REMOVE,
                        target_node=node_id,
                        before={"failure_rate": failure_rate},
                        after={"action": "remove_failing_node"},
                        rationale=f"Node {node_id} has {failure_rate:.0%} failure rate",
                    ))

        dominant_failure = telemetry.get("dominant_failure", "")
        if dominant_failure == "timeout":
            agent_nodes = [
                nid for nid, node in parent_workflow.nodes.items()
                if type(node).__name__ == "AgentNode"
            ]
            if agent_nodes:
                target = agent_nodes[0]
                current_timeout = getattr(parent_workflow.nodes[target], "timeout", 600)
                new_timeout = min((current_timeout or 600) * 2, 3600)
                proposals.append(MutationRecord(
                    operator=MutationType.PARAM_MUTATE,
                    target_node=target,
                    before={"timeout": current_timeout},
                    after={"timeout": new_timeout},
                    rationale="Dominant failure is timeout — increase timeout",
                ))

        diversity = archive_stats.get("diversity", 1.0)
        if isinstance(diversity, (int, float)) and diversity < 0.3:
            present_roles = {
                node.role.value  # type: ignore[union-attr]
                for node in parent_workflow.nodes.values()
                if hasattr(node, "role")
            }
            missing = set(AgentRole) - {AgentRole(r) for r in present_roles if r in [ar.value for ar in AgentRole]}
            if missing:
                new_role = next(iter(missing))
                proposals.append(MutationRecord(
                    operator=MutationType.NODE_INSERT,
                    target_node=None,
                    before={"present_roles": sorted(present_roles)},
                    after={"new_role": new_role.value},
                    rationale=f"Low diversity ({diversity:.2f}) — insert {new_role.value}",
                ))

        if not proposals:
            proposals.append(MutationRecord(
                operator=MutationType.PARAM_MUTATE,
                target_node=None,
                before={},
                after={"action": "explore"},
                rationale="No specific failure signal — propose parameter exploration",
            ))

        return proposals[:3]


def extract_telemetry(eval_result: EvalResult) -> dict[str, object]:
    """Extract structured diagnostics from an EvalResult.

    Returns a dict with:
    - node_stats: per-node success/failure data (from details if available)
    - dominant_failure: most common failure category
    - benchmark_score: the raw benchmark score
    - cost_usd: evaluation cost
    - complexity: workflow complexity metric
    """
    details = eval_result.details or {}

    node_stats: dict[str, object] = {}
    raw_stats = details.get("node_stats", {})
    if isinstance(raw_stats, dict):
        node_stats = dict(raw_stats)

    dominant_failure = ""
    raw_failure = details.get("dominant_failure", "")
    if isinstance(raw_failure, str):
        dominant_failure = raw_failure

    return {
        "node_stats": node_stats,
        "dominant_failure": dominant_failure,
        "benchmark_score": eval_result.benchmark_score,
        "hygiene_score": eval_result.hygiene_score,
        "cost_usd": eval_result.cost_usd,
        "complexity": eval_result.complexity,
        "score": eval_result.score,
    }


def _slug(text: str) -> str:
    """Convert text to a short slug for workflow naming."""
    clean = text.lower().replace(" ", "_")[:20]
    return "".join(c for c in clean if c.isalnum() or c == "_").strip("_") or "default"
