"""Frontend design mode — drop this into .factory/workflows/ to use.

Usage:
    cp examples/composed-modes/frontend_design.py /path/to/project/.factory/workflows/
    factory ceo /path/to/project --mode frontend-design --focus "GPU allocation card"

This composes standard factory packages with a frontend-specific
discovery step that finds design tokens, components, and patterns.
Edit the packages list to customize the pipeline.
"""

meta = {
    "name": "frontend-design",
    "description": "Design mode + frontend design system discovery",
}


def workflow():
    from factory.workflow.packages import (
        BUILD_RESEARCHERS,
        build_package,
        discovery_package,
        qa_package,
        research_package,
        strategy_package,
        study_package,
    )
    from factory.workflow.package import Package, Port, Sequential, StateContract
    from factory.workflow.primitives import AgentNode, AgentRole, Workflow

    # Frontend discovery — finds design system tokens, components, patterns
    frontend_discovery = AgentNode(
        id="frontend_discovery",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Discover the project's frontend design system. "
            "Find: design tokens (colors, spacing, typography), "
            "component library (React/Vue/Svelte components), "
            "layout patterns, data fetching conventions, "
            "and styling approach (CSS modules, Tailwind, styled-components). "
            "Write a structured design system reference to "
            ".factory/strategy/design-system.md."
        ),
        reads={".factory/strategy/study-combined.md"},
        writes={".factory/strategy/design-system.md"},
    )
    frontend_pkg = Package(
        name="frontend-discovery",
        version="1.0.0",
        description="Discover frontend design system",
        inputs=[Port(name="study", artifact_path=".factory/strategy/study-combined.md")],
        outputs=[Port(name="design-system", artifact_path=".factory/strategy/design-system.md")],
        contract=StateContract(
            requires=frozenset({"study_complete"}),
            produces=frozenset({"frontend_discovery_complete"}),
        ),
        graph=Workflow(
            name="frontend-discovery",
            nodes={"frontend_discovery": frontend_discovery},
            edges=[], start_node="frontend_discovery",
        ),
        entry_node="frontend_discovery",
        exit_node="frontend_discovery",
    )

    # Compose: discovery → study → frontend → research → strategy(user gate) → build → qa
    return Sequential(
        discovery_package(),
        study_package(),
        frontend_pkg,
        research_package(
            researchers=BUILD_RESEARCHERS,
            gate_prompt="Check research quality and frontend design system coverage.",
        ),
        strategy_package(
            research_reads={
                ".factory/strategy/research-similar.md",
                ".factory/strategy/research-techstack.md",
                ".factory/strategy/research-pitfalls.md",
                ".factory/strategy/design-system.md",
            },
            gate_type="user",
        ),
        build_package(),
        qa_package(),
        name="frontend-design",
    ).compile()
