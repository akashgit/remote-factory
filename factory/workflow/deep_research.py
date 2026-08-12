"""Deep-research single-agent iterative research workflow.

Runs study → deep_researcher (single agent with internal iteration loop) →
CEO coverage gate. Terminal mode — does not chain to build or improve.
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
        "Single-agent iterative research with built-in faithfulness checking "
        "and coverage evaluation. The researcher performs multiple rounds of "
        "WebSearch/WebFetch internally, following an inside-out protocol."
    ),
}

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
    "### Phase 2: Decompose into Sub-Questions\n\n"
    "Break the original prompt into 3-5 sub-questions. These must be:\n"
    "- Derived from the ORIGINAL PROMPT, not from previous search results\n"
    "- Shaped by internal findings (don't search for things the project already has)\n"
    "- Specific enough to produce actionable search queries\n\n"
    "Example: If the project already uses pytest, don't search for 'best testing framework'. "
    "Instead search for 'pytest advanced patterns for <specific need>'.\n\n"
    "### Phase 3: External Search\n\n"
    "For each sub-question:\n"
    "- Run 3-5 WebSearch queries with varied phrasing\n"
    "- WebFetch the 2-3 most promising pages from the results\n"
    "- Extract concrete findings: techniques, patterns, code examples, pitfalls\n"
    "- Note the source URL for every finding\n\n"
    "### Phase 4: Synthesize Running Report\n\n"
    "Merge external findings with internal state into a structured report:\n"
    "- Organize by topic, not by search query\n"
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
    "STOP that direction. Return to Phase 2 and decompose from the original "
    "prompt again.\n\n"
    "### Phase 6: Coverage Check\n\n"
    "After completing a search round, evaluate:\n"
    "- Are there major gaps in the research? Important aspects not yet covered?\n"
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
    "Safety-net review of the deep research report.\n\n"
    "Read the research report at .factory/strategy/research-combined.md.\n\n"
    "Check these four things:\n\n"
    "1. **Traceability:** Does every section trace back to the original "
    "research prompt? Are there sections answering questions nobody asked?\n\n"
    "2. **Grounding:** Are findings grounded in both external sources AND "
    "internal project context — not just generic advice?\n\n"
    "3. **Actionability:** Is the report actionable? "
    "Can concrete next steps be derived from it?\n\n"
    "4. **Citations:** Are claims cited with source URLs (external) or "
    "file paths (internal)?\n\n"
    "**Decision:**\n"
    "- PROCEED if the report is faithful, grounded, and actionable. "
    "Minor gaps are fine — the researcher has already done internal "
    "coverage checking.\n"
    "- RELOOP only if sections are missing or disconnected from the "
    "original prompt. In your verdict, list the specific gaps.\n\n"
    "This gate should almost always PROCEED — the researcher's internal "
    "faithfulness checks catch most issues. Only RELOOP for structural "
    "problems (missing sections, drift from prompt, no citations)."
)


def workflow() -> Workflow:
    """W₁₅: Deep Research Mode — single-agent iterative research with coverage checking.

    Study → deep_researcher (single AgentNode with internal iteration loop) →
    gate_coverage (CEO safety net).

    The researcher performs multiple rounds of search internally using WebSearch
    and WebFetch, with built-in faithfulness checking and coverage evaluation.
    The gate is a rare safety net — it should almost always PROCEED on first pass.

    Terminal mode — does not chain to build or improve.
    """
    nodes: dict[str, Any] = {}

    nodes["study"] = Study(
        id="study",
        command="factory study {project_path}",
        writes={".factory/strategy/observations.md"},
    )

    nodes["deep_researcher"] = AgentNode(
        id="deep_researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=_DEEP_RESEARCHER_PROMPT,
        reads={
            ".factory/strategy/observations.md",
        },
        writes={".factory/strategy/research-combined.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-combined.md",
                must_exist=True,
                min_size=500,
            )
        ],
    )

    nodes["gate_coverage"] = GateNode(
        id="gate_coverage",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=_GATE_COVERAGE_PROMPT,
        reads={".factory/strategy/research-combined.md"},
    )

    edges = [
        Edge(source="study", target="deep_researcher"),
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
