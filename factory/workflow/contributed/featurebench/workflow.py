"""FeatureBench mode — hybrid host/container execution pipeline.

6-node pipeline: researcher → strategist → adversarial_tester → builder → gate_tests → archivist
TDD pattern: adversarial_tester writes validation tests from the spec first,
builder implements until gate_tests passes them.

The adversarial_tester writes validation tests on the host from the problem
statement alone (before any code is written), then the builder implements the
feature targeting those tests. gate_tests runs the pre-written tests inside
the container via docker exec. RELOOP from gate_tests → builder (max 3).
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
        "FeatureBench mode — TDD pipeline for implementing features from interface specifications. "
        "Reads problem_statement.md, analyzes repo structure, creates an implementation plan, "
        "writes validation tests from the spec FIRST (adversarial_tester), then the builder "
        "implements until the tests pass. Uses hybrid host/container execution: adversarial_tester "
        "writes tests on the host before implementation, gate_tests runs them inside the container "
        "via docker exec. Supports iterative refinement (max 3 builder loops via gate_tests). "
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

    # ── Adversarial tester: write validation tests from spec (HOST) ──

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        reads={".factory/reviews/researcher-latest.md"},
        writes={".factory/reviews/adversarial-qa.md", ".factory/validation_tests/test_spec_compliance.py"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/adversarial-qa.md", must_exist=True),
        ],
        prompt_template=(
            "You are a TDD validation test writer for the FeatureBench benchmark.\n\n"
            "Your job is to READ the problem statement, SCAN the codebase for masked\n"
            "functions, and WRITE comprehensive pytest tests BEFORE any code is\n"
            "implemented. The builder will use these tests as a target.\n"
            "You do NOT run the tests — the gate_tests node runs them inside the container\n"
            "via docker exec.\n\n"
            "Steps:\n"
            "1. Read problem_statement.md for interface specs (function signatures, import\n"
            "   paths, types, expected behavior).\n"
            "2. Read .factory/reviews/researcher-latest.md for repo structure understanding\n"
            "   and the researcher's analysis of masked functions.\n"
            "3. CRITICAL — Scan the codebase for masked/blank function bodies. The benchmark\n"
            "   masks code by replacing function bodies with blank lines (empty bodies where\n"
            "   code should be). These masked helpers are in utility files, parsers, writers,\n"
            "   row accessors, etc. — NOT just the main interfaces from the problem statement.\n"
            "   Run: grep -rn 'def ' --include='*.py' | head -200\n"
            "   Then open files that the researcher identified as containing masked functions.\n"
            "   Look for functions whose body is just blank lines or 'pass' where real logic\n"
            "   should be. These are hidden dependencies that the builder MUST also implement.\n"
            "4. mkdir -p .factory/validation_tests/\n"
            "5. Write .factory/validation_tests/test_spec_compliance.py with comprehensive\n"
            "   pytest tests covering BOTH:\n"
            "   A) Every interface spec from problem_statement.md:\n"
            "      - Import paths matching the interface specs exactly\n"
            "      - Function signature tests (correct parameters, return types)\n"
            "      - Happy-path tests for each specified interface\n"
            "      - Edge-case tests (empty input, None, boundary values)\n"
            "   B) Masked helper functions found in step 3:\n"
            "      - Import and call each masked helper function\n"
            "      - Test that they return non-None / non-trivial results\n"
            "      - Test them with representative inputs based on their signatures\n"
            "      - These helpers are often in utility modules, XML writers, data parsers,\n"
            "        row/table accessors, strategy implementations, etc.\n"
            "   All tests must use test_ prefix and be self-contained.\n\n"
            "IMPORTANT:\n"
            "- Do NOT run pytest — the gate runs tests inside the container\n"
            "- Do NOT modify any source code — you are writing tests only\n"
            "- Do NOT reference builder code, git diff, or builder-latest.md\n"
            "  (the builder has NOT run yet — this is TDD)\n\n"
            "Write a summary to .factory/reviews/adversarial-qa.md listing ALL tests\n"
            "written — both interface spec tests and masked helper tests."
        ),
    )

    # ── Builder: implement the feature (HOST) ───────────────────────

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "CRITICAL: Your job is to implement or modify SOURCE CODE only. Do NOT create, "
            "modify, or recreate test files (tests/*). The FeatureBench evaluator provides "
            "its own test files — anything you write in tests/ will be overwritten. Read the "
            "interface descriptions in problem_statement.md and implement the code that "
            "satisfies them in the appropriate source files.\n\n"
            "Implement the FeatureBench feature according to the plan at "
            ".factory/strategy/current.md.\n\n"
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
            "5. The adversarial_tester has already written validation tests at\n"
            "   .factory/validation_tests/test_spec_compliance.py. The gate_tests node\n"
            "   will run them after you commit. Focus on making your implementation pass\n"
            "   these tests.\n\n"
            "6. BEFORE implementing, scan the ENTIRE codebase for masked function bodies.\n"
            "   The benchmark replaces function bodies with blank lines. Search for functions\n"
            "   whose body is empty/blank where real logic should be — especially in utility\n"
            "   modules, XML writers/parsers, data converters, row/table accessors, strategy\n"
            "   implementations, and helper files. These are NOT listed in problem_statement.md\n"
            "   but MUST be implemented for the main interfaces to work. Implement ALL masked\n"
            "   functions you find, not just the ones the validation tests directly test.\n\n"
            "7. If this is a RELOOP from gate_tests (test loop):\n"
            "   - Read .factory/reviews/gate-pytest-output.txt for the pytest failure output.\n"
            "     Each failure has a test name, error type, and traceback. Fix the root cause\n"
            "     and recommit.\n"
            "   - For each failure, READ THE FULL STACK TRACE. Follow it to the exact file\n"
            "     and line that errors. Open that file — if you find an empty/stub function\n"
            "     body (blank lines where code should be), IMPLEMENT IT. The benchmark masks\n"
            "     helper functions throughout the codebase, not just the described interfaces.\n"
            "   - Common masked dependencies: model methods, utility classes, threading\n"
            "     helpers, storage backends. AttributeError/NameError usually means a masked\n"
            "     function you haven't implemented yet.\n"
            "   - Do not rewrite working code — only fix what the stack traces point to.\n\n"
            "8. Commit all changes with: git add -A && git commit -m 'implement feature'\n"
            "   The FeatureBench harness extracts changes via git diff, so commits are required.\n\n"
            "Write a summary of what was implemented to .factory/reviews/builder-latest.md."
        ),
        reads={
            ".factory/strategy/current.md",
            ".factory/validation_tests/test_spec_compliance.py",
            ".factory/reviews/adversarial-qa.md",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
        ],
    )

    # ── Test gate: run validation tests inside container via docker exec ─

    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "if [ -f {project_path}/.factory/validation_tests/test_spec_compliance.py ]; then "
            "docker exec {container_name} rm -rf /tmp/validation_tests && "
            "docker cp {project_path}/.factory/validation_tests {container_name}:/tmp/validation_tests && "
            "docker exec {container_name} bash -c "
            "'cd /tmp/validation_tests && conda run -n testbed pytest . -x --tb=short -v 2>&1' "
            "> {project_path}/.factory/reviews/gate-pytest-output.txt 2>&1; "
            "RC=$?; "
            "if [ $RC -eq 0 ]; then echo 'pass: all validation tests passed'; "
            "elif [ $RC -eq 5 ]; then echo 'reloop: no tests collected'; "
            "else echo 'reloop: validation tests failed — see .factory/reviews/gate-pytest-output.txt'; fi; "
            "else echo 'reloop: no validation test files found'; fi"
        ),
        reads={
            ".factory/validation_tests/test_spec_compliance.py",
            ".factory/reviews/adversarial-qa.md",
        },
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
            "- .factory/reviews/adversarial-qa.md (pre-written validation tests)\n\n"
            "Record:\n"
            "1. Task outcome: resolved or not, number of builder iterations needed\n"
            "2. Successful strategies: what worked well\n"
            "3. Failure patterns: what caused test failures and how they were fixed\n"
            "4. Repository-specific notes: conventions, quirks, or patterns\n"
            "5. Transferable insights: patterns that would help on similar tasks\n\n"
            "Write to .factory/archive/featurebench-learnings.md."
        ),
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/archive/featurebench-learnings.md"},
        blocking=False,
    )

    # ── Edges ──────────────────────────────────────────────────────

    edges = [
        Edge(source="researcher", target="strategist"),
        Edge(source="strategist", target="adversarial_tester"),
        Edge(source="adversarial_tester", target="builder"),
        Edge(source="builder", target="gate_tests"),
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
