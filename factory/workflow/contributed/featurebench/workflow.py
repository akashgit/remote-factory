"""FeatureBench mode — hybrid host/container execution pipeline.

6-node pipeline with host-side orchestration and container-side execution:
  researcher (host) → strategist (host) → builder (container) →
    health_checker (container) → gate_tests → [RELOOP to builder, max 3] →
    archivist (host, async)

Host nodes (researcher, strategist, archivist) run on the host where Claude Code
is already available. Container nodes (builder, health_checker) run inside the
FeatureBench container via podman exec, accessing /testbed and conda env testbed.

The WorkflowExecutor routes container nodes via podman exec based on the
execution_context metadata field. File sync between host and container is handled
by the adapter via podman cp.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "featurebench",
    "description": (
        "FeatureBench mode — implement complete features from interface specifications. "
        "Reads problem_statement.md, analyzes repo structure, creates an implementation plan, "
        "builds the feature inside a container, then verifies via test suite. "
        "Uses hybrid host/container execution: orchestration agents run on the host, "
        "builder and health_checker run inside the container via podman exec. "
        "Supports iterative refinement (max 3 builder loops via gate_tests). "
        "Use when invoked with --mode featurebench."
    ),
}


def workflow() -> Workflow:
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Researcher: analyze problem + repo (HOST) ─────────────────

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze the FeatureBench problem statement and repository structure.\n\n"
            "Note: the test files have been removed by the benchmark harness. Focus on "
            "understanding what source code needs to be written/modified to implement the "
            "described interface. Do NOT plan to recreate test files.\n\n"
            "The problem statement is at {project_path}/problem_statement.md.\n"
            "Repository files are available at {project_path} for analysis.\n\n"
            "1. Read problem_statement.md thoroughly. Extract:\n"
            "   - Core Functionality overview\n"
            "   - Main Features and Requirements (enumerate each)\n"
            "   - Key Challenges (mandatory components)\n"
            "   - Interface Descriptions (file paths, import paths, function signatures, types)\n\n"
            "2. Study the existing repository structure:\n"
            "   - List all source files and their purposes\n"
            "   - Identify which modules/packages exist\n"
            "   - Map import dependencies between files\n"
            "   - Note any existing test infrastructure\n\n"
            "3. For L1 (incremental) tasks:\n"
            "   - Identify the extension points in the existing codebase\n"
            "   - Map where new code must integrate with existing code\n"
            "   - Note existing patterns (naming, error handling, logging) to follow\n"
            "   - CRITICAL: The benchmark masks code by replacing function bodies with blank\n"
            "     lines. Scan ALL files referenced by the interface specs for empty/stub\n"
            "     function bodies — these are hidden dependencies you must also implement.\n"
            "     Trace the call chain from each interface function to find masked helpers\n"
            "     in other files (models, utilities, threading helpers, etc.).\n\n"
            "4. For L2 (from-scratch) tasks:\n"
            "   - The repo may be nearly empty — plan the full project structure\n"
            "   - Note any README.md or configuration files that hint at expected structure\n\n"
            "5. Summarize:\n"
            "   - Files that need to be created (with exact paths from interface specs)\n"
            "   - Files that need to be modified\n"
            "   - Integration points and dependency order\n"
            "   - Potential challenges or ambiguities in the spec\n\n"
            "Write findings to .factory/reviews/researcher-latest.md."
        ),
        reads=set(),
        writes={".factory/reviews/researcher-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/researcher-latest.md", must_exist=True),
        ],
    )

    # ── Strategist: create implementation plan (HOST) ──────────────

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Create an implementation plan for this FeatureBench task.\n\n"
            "Note: the test files have been removed by the benchmark harness. Focus on "
            "understanding what source code needs to be written/modified to implement the "
            "described interface. Do NOT plan to recreate test files.\n\n"
            "The problem statement is at {project_path}/problem_statement.md.\n"
            "All file paths in your plan must be relative to the project root.\n\n"
            "Read the researcher's analysis at .factory/reviews/researcher-latest.md.\n"
            "Read the problem statement at problem_statement.md.\n\n"
            "Produce a plan with:\n\n"
            "1. **File Creation Order** — list every file to create, in dependency order\n"
            "   (files with no internal deps first, files that import from them later).\n"
            "   For each file: exact path, what it implements, which interface spec it satisfies.\n\n"
            "2. **File Modification Plan** — for each existing file that needs changes:\n"
            "   what to add/modify and why.\n\n"
            "3. **Interface Compliance Checklist** — for each interface spec in the problem\n"
            "   statement, list: the file, the function/class signature, the expected behavior.\n"
            "   The builder MUST match these exactly.\n\n"
            "4. **Test Strategy** — which F2P tests validate which features.\n\n"
            "5. **Risk Areas** — parts of the spec that are ambiguous or could cause P2P\n"
            "   regressions.\n\n"
            "Write the plan to .factory/strategy/current.md."
        ),
        reads={".factory/reviews/researcher-latest.md"},
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(path=".factory/strategy/current.md", must_exist=True),
        ],
    )

    # ── Builder: implement the feature (CONTAINER) ─────────────────

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        max_iterations=3,
        metadata={"execution_context": "container"},
        prompt_template=(
            "CRITICAL: Your job is to implement or modify SOURCE CODE only. Do NOT create, "
            "modify, or recreate test files (tests/*). The FeatureBench evaluator provides "
            "its own test files — anything you write in tests/ will be overwritten. Read the "
            "interface descriptions in problem_statement.md and implement the code that "
            "satisfies them in the appropriate source files.\n\n"
            "Implement the FeatureBench feature according to the plan at "
            ".factory/strategy/current.md.\n\n"
            "You are running inside a container at /testbed with conda env testbed.\n"
            "The working directory is {project_path} — a git-tracked repository.\n"
            "You MUST commit all changes so the FeatureBench harness can extract your git diff.\n\n"
            "CRITICAL RULES:\n"
            "1. Read problem_statement.md for the authoritative interface specifications.\n"
            "   Match function signatures, class names, import paths, and types EXACTLY.\n\n"
            "2. Follow the file creation order from the plan. Create files in dependency\n"
            "   order — files with no internal imports first.\n\n"
            "3. Multi-file implementation is expected (~15 files average). Do not try to\n"
            "   put everything in one file. Follow the repo's existing structure and\n"
            "   naming conventions.\n\n"
            "4. ANTI-CHEATING COMPLIANCE (MANDATORY):\n"
            "   - Do NOT access /usr/local/lib/python* paths (gold solution location)\n"
            "   - Do NOT fetch from any blacklisted URLs\n"
            "   - Do NOT read or reference any test files to reverse-engineer expected outputs\n"
            "   - Implement from the problem statement and interface specs ONLY\n\n"
            "5. Do NOT run the test suite yourself. The health_checker node runs\n"
            "   tests later in the pipeline — focus your time on implementation.\n\n"
            "6. If this is a RELOOP from gate_tests (test loop):\n"
            "   - Read the test failure output from .factory/reviews/health-check.md\n"
            "   - For each failure, READ THE FULL STACK TRACE. Follow it to the exact file\n"
            "     and line that errors. Open that file — if you find an empty/stub function\n"
            "     body (blank lines where code should be), IMPLEMENT IT. The benchmark masks\n"
            "     helper functions throughout the codebase, not just the described interfaces.\n"
            "   - Common masked dependencies: model methods, utility classes, threading\n"
            "     helpers, storage backends. AttributeError/NameError usually means a masked\n"
            "     function you haven't implemented yet.\n"
            "   - Do not rewrite working code — only fix what the stack traces point to.\n\n"
            "7. Commit all changes with: git add -A && git commit -m 'implement feature'\n"
            "   The FeatureBench harness extracts changes via git diff, so commits are required.\n\n"
            "Write a summary of what was implemented to .factory/reviews/builder-latest.md."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
        ],
    )

    # ── Health checker: run tests (CONTAINER) ──────────────────────

    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        timeout=600,
        metadata={"execution_context": "container"},
        prompt_template=(
            "Run the test suite to verify the FeatureBench implementation.\n\n"
            "You are running inside a container at /testbed with conda env testbed.\n"
            "The working directory is {project_path}.\n\n"
            "1. Run the project's test suite. Look for pytest or unittest configuration\n"
            "   in the repo. Run ALL tests — both F2P (feature validation) and P2P\n"
            "   (regression) tests. Use `conda run -n testbed pytest` or the project's\n"
            "   configured test command.\n\n"
            "2. Parse the test output and report:\n"
            "   - F2P tests: X passed / Y total\n"
            "   - P2P tests: X passed / Y total\n"
            "   - Any error messages or stack traces from failures\n\n"
            "3. Determine the verdict:\n"
            "   - RESOLVED: ALL F2P pass AND ALL P2P pass -> write 'RESOLVED: true'\n"
            "   - NOT RESOLVED: any test fails -> write 'RESOLVED: false'\n"
            "   - Include the specific test names and failure reasons for any failures\n\n"
            "4. If tests cannot be run (missing dependencies, import errors), report\n"
            "   the setup issue clearly so the builder can fix it on reloop.\n\n"
            "Write the full test report to .factory/reviews/health-check.md.\n\n"
            "IMPORTANT: Include the marker line 'RESOLVED: true' or 'RESOLVED: false'\n"
            "at the top of the report — the downstream gate parses this."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/health-check.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/health-check.md", must_exist=True),
        ],
    )

    # ── Test gate: all tests pass? ─────────────────────────────────

    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "if grep -q 'RESOLVED: true' "
            "{project_path}/.factory/reviews/health-check.md; "
            "then echo 'PROCEED'; else echo 'RELOOP'; fi"
        ),
        reads={".factory/reviews/health-check.md"},
    )

    # ── Archivist: record learnings (HOST, async) ──────────────────

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        model="haiku",
        prompt_template=(
            "Archive learnings from this FeatureBench task.\n\n"
            "Read:\n"
            "- .factory/strategy/current.md (implementation plan)\n"
            "- .factory/reviews/builder-latest.md (what was built)\n"
            "- .factory/reviews/health-check.md (test results)\n\n"
            "Record:\n"
            "1. Task outcome: resolved or not, number of builder iterations needed\n"
            "2. Successful strategies: what worked well\n"
            "3. Failure patterns: what caused test failures and how they were fixed\n"
            "4. Repository-specific notes: conventions, quirks, or patterns\n"
            "5. Transferable insights: patterns that would help on similar tasks\n\n"
            "Write to .factory/archive/featurebench-learnings.md."
        ),
        reads=set(),
        writes={".factory/archive/featurebench-learnings.md"},
        blocking=False,
    )

    # ── Edges ──────────────────────────────────────────────────────

    edges = [
        Edge(source="researcher", target="strategist"),
        Edge(source="strategist", target="builder"),
        Edge(source="builder", target="health_checker"),
        Edge(source="health_checker", target="gate_tests"),
        Edge(source="gate_tests", target="archivist", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ───────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "featurebench"

    return Workflow(
        name="featurebench",
        nodes=nodes,
        edges=edges,
        start_node="researcher",
        terminal=True,
        trigger=trigger,
    )
