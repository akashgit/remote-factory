"""Convert WorkflowSkill (Pydantic graph) → Claude Code SKILL.md files.

The converter parses the graph structure — nodes, edges, gates, fork/join
topology — and generates standardized prose instructions. Two execution
formats from one source: flexible prose (SKILL.md) for interactive use,
rigid graph (WorkflowExecutor) for headless automation.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path

import structlog

from factory.workflow.primitives import (
    AgentNode,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    Study,
    VerdictType,
    Workflow,
)

log = structlog.get_logger()


# ── metadata per workflow (enriched descriptions, phases, triggers) ──


WORKFLOW_META: dict[str, dict[str, str | list[str]]] = {
    "build": {
        "description": (
            "Build a new project from scratch. Runs parallel research, strategy "
            "synthesis, implementation, QA verification, and archival. Use when "
            "the user says 'build X', 'create X', or the project state is no_repo "
            "or incomplete."
        ),
        "argument_hint": "<project_path> [idea or spec]",
    },
    "design": {
        "description": (
            "Interactive design mode — identical to build but with a user approval "
            "gate at strategy. Use when the user says 'design X', 'plan X', "
            "'let's discuss what to build', or wants to review the strategy before building."
        ),
        "argument_hint": "<project_path> [idea or spec]",
    },
    "improve": {
        "description": (
            "Improve an existing project through systematic experimentation. "
            "Runs study, research, hypothesis generation, build/eval loop, and archival. "
            "Use when the user says 'improve X', 'make X better', or the project "
            "state is has_factory."
        ),
        "argument_hint": "<project_path> [--focus <target>]",
    },
    "research": {
        "description": (
            "Research mode — extends improve with baseline measurement, failure analysis, "
            "research-command eval, and plateau detection. Use when the project has "
            "research_target configured and the user says 'research X' or wants "
            "metric-driven optimization."
        ),
        "argument_hint": "<project_path>",
    },
    "meta": {
        "description": (
            "Meta mode — cross-project insights, playbook evolution, and test pruning. "
            "Use when the user says 'meta', 'self-improve', 'evolve playbooks', "
            "or wants to improve the factory's own agents."
        ),
        "argument_hint": "<project_path>",
    },
}


# ── topological sort ────────────────────────────────────────────


def _topological_sort(workflow: Workflow) -> list[str]:
    """Topological sort of node IDs respecting edge directions.

    Handles RELOOP edges by ignoring back-edges (edges whose target
    is already visited) and treating conditional edges as optional paths.
    Returns nodes in execution order.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in workflow.nodes}

    back_edges: set[tuple[str, str]] = set()
    for edge in workflow.edges:
        if edge.condition == VerdictType.RELOOP:
            back_edges.add((edge.source, edge.target))
            continue
        adj[edge.source].append(edge.target)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    queue: deque[str] = deque()
    for nid in workflow.nodes:
        if in_degree.get(nid, 0) == 0:
            queue.append(nid)

    if not queue:
        queue.append(workflow.start_node)

    ordered: list[str] = []
    visited: set[str] = set()

    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        ordered.append(nid)

        for target in adj.get(nid, []):
            in_degree[target] -= 1
            if in_degree[target] <= 0 and target not in visited:
                queue.append(target)

    for nid in workflow.nodes:
        if nid not in visited:
            ordered.append(nid)

    return ordered


# ── node → instruction converters ──────────────────────────────


def _agent_to_instruction(node: AgentNode, *, is_parallel: bool = False) -> str:
    """Convert an AgentNode to a CLI invocation instruction."""
    role = node.role.value
    timeout = 600 if role != "archivist" else 300
    model_flag = " --model haiku" if role == "archivist" else ""

    prompt = node.prompt_template or f"Execute {role} task for the project."

    if node.reads:
        reads_str = ", ".join(sorted(node.reads))
        prompt += f"\nRead: {reads_str}"
    if node.writes:
        writes_str = ", ".join(sorted(node.writes))
        prompt += f"\nWrite output to: {writes_str}"

    bg_suffix = " &" if not node.blocking else ""
    tag_flag = ""
    if is_parallel and role == "researcher":
        tag = node.id.replace("researcher_", "")
        tag_flag = f" --review-tag {tag}"

    cmd = (
        f'factory agent {role}{tag_flag} --task "{prompt}"'
        f' --project "$PROJECT_PATH" --timeout {timeout}{model_flag}{bg_suffix}'
    )

    lines = [f"```bash\n{cmd}\n```"]

    if not node.blocking:
        lines.append("*(fire-and-forget — CEO continues immediately)*")

    return "\n".join(lines)


def _fn_to_instruction(node: FnNode) -> str:
    """Convert an FnNode to a CLI command instruction.

    Eval commands are delegated to the evaluator agent rather than
    invoked directly — the QA pipeline requires agent-mediated eval.
    """
    cmd = node.command.replace("{project_path}", "$PROJECT_PATH")
    if "factory eval" in cmd:
        return (
            '```bash\nfactory agent evaluator --task "Run eval and report results." '
            '--project "$PROJECT_PATH" --timeout 300\n```'
        )
    return f"```bash\n{cmd}\n```"


def _study_to_instruction(node: Study) -> str:
    """Convert a Study node to a factory study instruction."""
    cmd = node.command.replace("{project_path}", "$PROJECT_PATH")
    focus = ""
    if node.focus:
        focus = f' --focus "{node.focus}"'
    return (
        f"Run local study to gather observations:\n\n"
        f"```bash\n{cmd}{focus}\n```\n\n"
        f"Writes observations to `.factory/strategy/observations.md`."
    )


def _gate_to_checkpoint(
    node: GateNode,
    reloop_edges: list[Edge],
) -> str:
    """Convert a GateNode to a steering checkpoint."""
    gate_name = node.id.replace("gate_", "").replace("_", " ").title()

    lines: list[str] = []

    if node.evaluator_type == "user":
        lines.append(f"### Steering Point — {gate_name} (User Approval)")
        lines.append("")
        lines.append("Present findings to the user. Wait for approval or feedback.")
        lines.append("- **Approve** → proceed to next step")
        lines.append("- **Feedback** → re-run the previous step with corrections")
    elif node.evaluator_type == "fn":
        lines.append(f"### Gate — {gate_name} (Automated)")
        lines.append("")
        if node.evaluator_command:
            cmd = node.evaluator_command.replace("{project_path}", "$PROJECT_PATH")
            lines.append(f"```bash\n{cmd}\n```")
    else:
        lines.append(f"### CEO Review — {gate_name}")
        lines.append("")
        lines.append("Apply the CEO Review Gate protocol:")
        lines.append("1. Read the agent output for the preceding step")
        if node.reads:
            reads = ", ".join(f"`{r}`" for r in sorted(node.reads))
            lines.append(f"2. Read artifacts: {reads}")
        if node.gate_prompt:
            lines.append(f"3. Assess: {node.gate_prompt}")
        lines.append(
            f"4. Write verdict to `.factory/reviews/ceo-verdict-{gate_name.lower().replace(' ', '-')}.md`"
        )
        lines.append("5. **PROCEED** → continue to next step")
        lines.append("6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)")
        lines.append("7. **ABORT** → log failure and skip to archival")

    for edge in reloop_edges:
        max_iter = 3
        for e2 in reloop_edges:
            if e2.source == node.id and e2.condition == VerdictType.RELOOP:
                pass
        lines.append(f"\n*On RELOOP: return to `{edge.target}` (max {max_iter} iterations)*")

    return "\n".join(lines)


def _fork_to_instruction(node: ForkNode, workflow: Workflow) -> str:
    """Convert a ForkNode to parallel agent spawning instructions."""
    lines = [f"Spawn {len(node.targets)} agents in parallel:\n"]

    for target_id in node.targets:
        target_node = workflow.nodes.get(target_id)
        if isinstance(target_node, AgentNode):
            lines.append(_agent_to_instruction(target_node, is_parallel=True))
            lines.append("")

    lines.append("```bash\nwait\n```")
    return "\n".join(lines)


def _join_to_instruction(node: JoinNode) -> str:
    """Convert a JoinNode to a wait-for-all instruction."""
    sources = ", ".join(f"`{s}`" for s in node.sources)
    lines = [f"Wait for all parallel agents to complete: {sources}"]
    if node.reads:
        reads = ", ".join(f"`{r}`" for r in sorted(node.reads))
        lines.append(f"\nRead combined outputs: {reads}")
    if node.writes:
        writes = ", ".join(f"`{w}`" for w in sorted(node.writes))
        lines.append(f"\nWrite combined result to: {writes}")
    return "\n".join(lines)


# ── frontmatter builder ────────────────────────────────────────


def _build_frontmatter(
    name: str,
    description: str,
    argument_hint: str | None = None,
) -> str:
    """Build SKILL.md YAML frontmatter."""
    lines = [
        "---",
        f"name: workflow-{name}",
        f'description: "{description}"',
        "disable-model-invocation: true",
    ]
    if argument_hint:
        lines.append(f'argument-hint: "{argument_hint}"')
    lines.append("---")
    return "\n".join(lines)


# ── main converter ──────────────────────────────────────────────


def workflow_to_skill_md(workflow: Workflow) -> str:
    """Convert a Workflow into a Claude Code SKILL.md string.

    Parses the workflow graph structure (nodes, edges, gates, fork/join)
    and generates standardized prose instructions that the CEO follows
    flexibly. Gates become steering points for user interaction.
    """
    name = workflow.name
    meta = WORKFLOW_META.get(name, {})
    description = str(meta.get("description", f"Run the {name} workflow."))
    argument_hint = str(meta.get("argument_hint", "<project_path>"))

    frontmatter = _build_frontmatter(name, description, argument_hint)

    title = name.replace("_", " ").title()
    header = f"# {title} Workflow\n\nThe user wants: **$ARGUMENTS**"

    reloop_map: dict[str, list[Edge]] = defaultdict(list)
    for edge in workflow.edges:
        if edge.condition == VerdictType.RELOOP:
            reloop_map[edge.source].append(edge)

    sorted_nodes = _topological_sort(workflow)
    fork_targets: set[str] = set()
    for nid in sorted_nodes:
        node = workflow.nodes[nid]
        if isinstance(node, ForkNode):
            fork_targets.update(node.targets)

    sections: list[str] = []
    phase_num = 1

    for nid in sorted_nodes:
        if nid in fork_targets:
            continue

        node = workflow.nodes[nid]

        if isinstance(node, ForkNode):
            node_title = nid.replace("fork_", "").replace("_", " ").title()
            sections.append(f"## Phase {phase_num}: {node_title} (Parallel)\n")
            sections.append(_fork_to_instruction(node, workflow))
            phase_num += 1

        elif isinstance(node, JoinNode):
            node_title = nid.replace("join_", "").replace("_", " ").title()
            sections.append(f"## Barrier: {node_title}\n")
            sections.append(_join_to_instruction(node))

        elif isinstance(node, GateNode):
            sections.append(
                _gate_to_checkpoint(node, reloop_map.get(nid, []))
            )

        elif isinstance(node, Study):
            node_title = "Observe"
            sections.append(f"## Phase {phase_num}: {node_title}\n")
            sections.append(_study_to_instruction(node))
            phase_num += 1

        elif isinstance(node, AgentNode):
            role_title = node.role.value.replace("_", " ").title()
            node_title = nid.replace("_", " ").title()
            if role_title.lower() in node_title.lower():
                section_title = node_title
            else:
                section_title = f"{role_title} — {node_title}"
            sections.append(f"## Phase {phase_num}: {section_title}\n")
            sections.append(_agent_to_instruction(node))
            phase_num += 1

        elif isinstance(node, FnNode):
            node_title = nid.replace("_", " ").title()
            sections.append(f"## Step: {node_title}\n")
            sections.append(_fn_to_instruction(node))

    body = "\n\n".join(sections)

    result = f"{frontmatter}\n\n{header}\n\n{body}\n"

    line_count = result.count("\n") + 1
    if line_count > 500:
        log.warning(
            "skill_export.oversized",
            workflow=name,
            lines=line_count,
            limit=500,
        )

    return result


# ── bulk export ─────────────────────────────────────────────────


def export_all_skills(
    output_dir: Path,
    workflows: dict[str, Workflow] | None = None,
) -> list[Path]:
    """Export all registered workflows as SKILL.md files.

    Writes each to output_dir/workflow-<name>/SKILL.md.
    Returns paths to generated files.
    """
    if workflows is None:
        from factory.workflow.definitions import register_all
        workflows = register_all()

    generated: list[Path] = []

    for name, wf in workflows.items():
        skill_md = workflow_to_skill_md(wf)

        skill_dir = output_dir / f"workflow-{name}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(skill_md)

        generated.append(skill_path)
        log.info("skill_export.wrote", path=str(skill_path), lines=skill_md.count("\n") + 1)

    return generated


# ── validation ──────────────────────────────────────────────────


def validate_skill(content: str) -> list[str]:
    """Validate a generated SKILL.md string. Returns list of issues."""
    issues: list[str] = []

    if not content.startswith("---"):
        issues.append("Missing frontmatter (must start with ---)")
        return issues

    parts = content.split("---", 2)
    if len(parts) < 3:
        issues.append("Malformed frontmatter (missing closing ---)")
        return issues

    fm = parts[1]

    name_match = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
    if not name_match:
        issues.append("Missing 'name' in frontmatter")
    else:
        name_val = name_match.group(1).strip()
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", name_val):
            issues.append(f"Name '{name_val}' is not valid kebab-case (1-64 chars, a-z0-9-)")

    desc_match = re.search(r'^description:\s*"(.+)"$', fm, re.MULTILINE)
    if not desc_match:
        issues.append("Missing 'description' in frontmatter")
    else:
        desc_val = desc_match.group(1)
        if len(desc_val) > 1024:
            issues.append(f"Description exceeds 1024 chars ({len(desc_val)})")

    line_count = content.count("\n") + 1
    if line_count > 500:
        issues.append(f"Body exceeds 500 lines ({line_count})")

    return issues
