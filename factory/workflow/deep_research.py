"""Deep-research iterative research workflow with decomposition.

Runs study → decomposer → deep_researcher → CEO coverage gate.
The decomposer generates research directions; the researcher executes them.
Terminal mode — does not chain to build or improve.
Triggered via `factory workflow run deep-research` or
`factory ceo /path --mode deep-research`.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    GateNode,
    Study,
    VerdictType,
    Workflow,
)

meta = {
    "name": "deep-research",
    "description": (
        "Iterative research with decomposition, faithfulness checking, and "
        "coverage evaluation. A decomposer generates research directions; "
        "the researcher executes them with multiple rounds of "
        "WebSearch/WebFetch, following an inside-out protocol."
    ),
}

_DECOMPOSER_PROMPT = (
    "You are the Research Decomposer. Produce 3-5 research directions tailored "
    "to the current mode and project context.\n\n"
    "Read:\n"
    "- The CEO's task (contains the original prompt and mode context)\n"
    "- .factory/strategy/observations.md (if exists — project state)\n"
    "- .factory/config.json (if exists — project config, research_target)\n\n"
    "Based on what you find, determine the research context:\n"
    "- New project (no .factory/) → web-focused directions (similar, tech, pitfalls)\n"
    "- Existing project, improve → mixed directions (internal assessment first, then "
    "targeted external search for weak dimensions)\n"
    "- Factory itself, create mode → code-focused directions (read existing patterns, "
    "parse mode intent, minimal web for novel patterns only)\n"
    "- Research target configured → failure-focused directions (within mutable surfaces)\n\n"
    "For each direction, write:\n\n"
    "### Direction N: [title]\n"
    "- **What to research:** specific question, not generic\n"
    "- **Why it matters:** how this connects to the original prompt and project\n"
    "- **Type:** internal (code/project reading), external (web search), or mixed\n"
    "- **Coverage signal:** how the researcher knows this direction is adequately covered\n\n"
    "Rules:\n"
    "- Directions must be derived from the ORIGINAL PROMPT\n"
    "- If the project already uses pytest, don't direct 'research testing frameworks'\n"
    "- Each direction should produce findings the strategist can act on\n"
    "- 3-5 directions maximum\n"
    "- Specify type (internal/external/mixed) so the researcher knows whether to "
    "read code or search the web\n\n"
    "Write to .factory/strategy/research-directions.md"
)

_DEEP_RESEARCHER_PROMPT = (
    "You are the Deep Researcher — a single agent performing iterative, "
    "coverage-checked research. You have access to WebSearch and WebFetch. "
    "Your job is to produce a comprehensive, faithful research report by "
    "performing multiple rounds of search internally.\n\n"
    "## ORIGINAL PROMPT\n\n"
    "The research topic is provided in the CEO's task. Read it carefully — "
    "this is the anchor for ALL your research. Every finding must trace back "
    "to this prompt.\n\n"
    "## RESEARCH PROTOCOL — FOLLOW EXACTLY\n\n"
    "### Phase 1: Internal Research (FIRST — before any web search)\n\n"
    "Read internal project state to understand what already exists:\n"
    "- Read .factory/strategy/observations.md from factory study\n"
    "- Check .factory/archive/ for prior knowledge, past experiments, learnings\n"
    "- Read .factory/strategy/backlog.md if it exists\n"
    "- Understand frameworks, patterns, and constraints the project already uses\n"
    "- If research_target is configured in .factory/config.json, read "
    "mutable_surfaces, fixed_surfaces, and constraints\n\n"
    "Write a summary of what you found internally. This shapes your external search.\n\n"
    "### Phase 2: Read Research Directions\n\n"
    "Read .factory/strategy/research-directions.md — the decomposer has already "
    "generated 3-5 research directions for you.\n"
    "- These are your sub-questions — follow them\n"
    "- Note each direction's type (internal/external/mixed)\n"
    "- You may add follow-up sub-questions in later iterations based on gaps, "
    "but initial directions come from the decomposer\n\n"
    "### Phase 3: External Search\n\n"
    "For each direction marked external or mixed:\n"
    "- Run 3-5 WebSearch queries with varied phrasing\n"
    "- WebFetch the 2-3 most promising pages from the results\n"
    "- Extract concrete findings: techniques, patterns, code examples, pitfalls\n"
    "- Note the source URL for every finding\n"
    "For internal directions: read the specified code/files instead of searching.\n"
    "Don't search for things the project already has.\n\n"
    "### Phase 4: Synthesize Running Report\n\n"
    "Merge external findings with internal state into a structured report:\n"
    "- Organize by topic, not by search query or direction number\n"
    "- Connect each external finding to something concrete in the codebase\n"
    "- Generic advice without project grounding is noise — cut it\n\n"
    "### Phase 5: Faithfulness Check (MANDATORY — every iteration)\n\n"
    "After each search round, answer these three questions honestly:\n\n"
    "1. **Relevance:** 'Does this finding help answer the ORIGINAL PROMPT, "
    "or did I follow an interesting tangent?' — if tangent, discard and refocus\n\n"
    "2. **Grounding:** 'Is this finding connected to something concrete in the "
    "codebase, or is it generic advice?' — generic advice without project "
    "grounding is noise\n\n"
    "3. **Drift detection:** 'Are my follow-up sub-questions derived from the "
    "ORIGINAL PROMPT, or derived from previous search results?' — if next "
    "sub-question wouldn't make sense without reading previous results, "
    "you're drifting\n\n"
    "**Hard rule:** If 2 of last 3 search rounds fail the relevance check, "
    "STOP that direction. Return to Phase 2 and pick the next direction.\n\n"
    "### Phase 6: Coverage Check\n\n"
    "After completing a search round, evaluate:\n"
    "- Check each direction from research-directions.md: adequately covered?\n"
    "- If gaps remain → go back to Phase 3 with targeted sub-questions for "
    "the gaps\n"
    "- If coverage is sufficient → proceed to Phase 7\n"
    "- If two consecutive rounds produce no new findings → finalize (diminishing returns)\n"
    "- If you've used ~25 WebSearch calls total → finalize (search budget exhausted)\n\n"
    "### Phase 7: Final Report Check\n\n"
    "Before writing the final output:\n"
    "1. Re-read the original prompt verbatim\n"
    "2. For each section in your report, write one sentence explaining how it "
    "answers the original prompt — if you can't write that sentence, cut "
    "the section\n"
    "3. Verify every claim cites a source: URL (external) or file path (internal) "
    "— unsourced claims are low-confidence, mark them as such\n\n"
    "## OUTPUT\n\n"
    "Write the complete research report to .factory/strategy/research-combined.md\n\n"
    "Structure:\n"
    "- **Research Topic:** (restate the original prompt)\n"
    "- **Internal Context:** (summary of project state relevant to the topic)\n"
    "- **Findings by Topic:** (organized sections, each with citations)\n"
    "- **Gaps & Limitations:** (what you couldn't find or didn't cover)\n"
    "- **Recommendations:** (actionable next steps grounded in findings)\n\n"
    "## RELOOP HANDLING\n\n"
    "If .factory/strategy/research-combined.md already exists (from a prior "
    "iteration due to CEO gate RELOOP), read it as your starting report. "
    "Read .factory/reviews/ceo-verdict-coverage.md for the CEO's gap analysis. "
    "Focus on filling the specific gaps identified — do NOT restart from scratch."
)

_GATE_COVERAGE_PROMPT = (
    "Check the deep research report against the research directions.\n\n"
    "Read .factory/strategy/research-directions.md (what was asked for) and "
    ".factory/strategy/research-combined.md (what was produced).\n\n"
    "For each direction the decomposer specified:\n"
    "1. Is it covered in the research report?\n"
    "2. Is the coverage adequate (actually researched, not just mentioned)?\n"
    "3. Did the researcher stay within the direction's scope?\n\n"
    "Also check:\n"
    "4. Does the report trace back to the original prompt?\n"
    "5. Are findings grounded (connected to codebase, not generic advice)?\n"
    "6. Are claims cited with URLs or file paths?\n\n"
    "PROCEED if all directions are covered.\n"
    "RELOOP listing which directions are missing or inadequately covered."
)


def workflow() -> Workflow:
    """W₁₅: Deep Research Mode — decompose-then-research with coverage checking.

    Study → decomposer (generates research directions) →
    deep_researcher (executes directions with internal iteration) →
    gate_coverage (CEO safety net checking per-direction coverage).

    The decomposer produces 3-5 research directions. The researcher executes
    them using WebSearch/WebFetch with built-in faithfulness checking. The gate
    checks coverage against the original directions.

    Terminal mode — does not chain to build or improve.
    """
    nodes: dict[str, Any] = {}

    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    nodes["decomposer"] = AgentNode(
        id="decomposer",
        role=AgentRole.RESEARCHER,
        prompt_template=_DECOMPOSER_PROMPT,
        reads={".factory/strategy/observations.md"},
        writes={".factory/strategy/research-directions.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-directions.md",
                must_exist=True,
                min_size=200,
            )
        ],
        model="sonnet",
        timeout=120,
    )

    nodes["deep_researcher"] = AgentNode(
        id="deep_researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=_DEEP_RESEARCHER_PROMPT,
        reads={
            ".factory/strategy/observations.md",
            ".factory/strategy/research-directions.md",
        },
        writes={".factory/strategy/research-combined.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-combined.md",
                must_exist=True,
                min_size=500,
            )
        ],
        timeout=1800,
    )

    nodes["gate_coverage"] = GateNode(
        id="gate_coverage",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=_GATE_COVERAGE_PROMPT,
        reads={
            ".factory/strategy/research-directions.md",
            ".factory/strategy/research-combined.md",
        },
    )

    edges = [
        Edge(source="study", target="decomposer"),
        Edge(source="decomposer", target="deep_researcher"),
        Edge(source="deep_researcher", target="gate_coverage"),
        Edge(source="gate_coverage", target="deep_researcher", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return state == ProjectState.HAS_FACTORY and ctx.get("mode") == "deep-research"

    return Workflow(
        name="deep-research",
        nodes=nodes,
        edges=edges,
        start_node="study",
        trigger=trigger,
        terminal=True,
    )
