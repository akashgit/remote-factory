"""FeatureBench mode — solve FeatureBench tasks via two-loop QA pipeline.

10-node pipeline with two feedback loops:
  researcher → strategist → builder → code_reviewer → gate_review →
    adversarial_tester → gate_qa → [RELOOP to builder, max 3] →
    health_checker → gate_tests → [RELOOP to builder, max 3] →
    archivist (async)

QA loop catches interface mismatches cheaply before running full test suite.
Test loop runs F2P + P2P tests for the definitive verdict.

Designed for FeatureBench containers where:
- Problem statement is at {project_path}/problem_statement.md
- Solutions must match interface specifications exactly
- Evaluation uses fail-to-pass + pass-to-pass tests
- No .factory/ experiment infrastructure (no eval, no begin/finalize)
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
        "builds the feature across multiple files, then verifies via a two-loop QA pipeline: "
        "code review + adversarial testing catch interface/logic issues early, "
        "then F2P/P2P test suites provide the definitive verdict. "
        "Supports iterative refinement (max 3 builder loops per gate). "
        "Use when invoked with --mode featurebench."
    ),
}


def workflow() -> Workflow:
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Researcher: analyze problem + repo ──────────────────────────

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze the FeatureBench problem statement and repository structure.\n\n"
            "You are running inside a FeatureBench Docker container. The working directory\n"
            "is {project_path} — a git-tracked repository where you must implement a feature.\n"
            "The problem statement has been written to {project_path}/problem_statement.md\n"
            "by the FeatureBench harness.\n\n"
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
            "   - Note existing patterns (naming, error handling, logging) to follow\n\n"
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
        reads={"problem_statement.md"},
        writes={".factory/reviews/researcher-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/researcher-latest.md", must_exist=True),
        ],
    )

    # ── Strategist: create implementation plan ──────────────────────

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Create an implementation plan for this FeatureBench task.\n\n"
            "You are running inside a FeatureBench Docker container. The working directory\n"
            "is {project_path}. All file paths in your plan must be relative to this directory.\n\n"
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

    # ── Builder: implement the feature ──────────────────────────────

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=1200,
        max_iterations=3,
        prompt_template=(
            "Implement the FeatureBench feature according to the plan at "
            ".factory/strategy/current.md.\n\n"
            "You are running inside a FeatureBench Docker container. The working directory\n"
            "is {project_path} — this is a git-tracked repository. You MUST commit all\n"
            "changes here so the FeatureBench harness can extract your git diff.\n\n"
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
            "5. After implementing, run the test suite to check your work:\n"
            "   - Run F2P tests first to verify feature correctness\n"
            "   - Run P2P tests to verify no regressions\n"
            "   - If tests fail, read the error output carefully and fix\n\n"
            "6. If this is a RELOOP from gate_qa (QA loop):\n"
            "   - Read code review issues from .factory/reviews/code-review.md\n"
            "   - Read adversarial test results from .factory/reviews/adversarial-qa.md\n"
            "   - Fix the specific issues found — interface mismatches, logic errors,\n"
            "     spec non-compliance\n"
            "   - Do not rewrite working code\n\n"
            "7. If this is a RELOOP from gate_tests (test loop):\n"
            "   - Read the test failure output from .factory/reviews/health-check.md\n"
            "   - Focus on fixing the specific failures — do not rewrite working code\n"
            "   - Common issues: wrong return types, missing edge cases, import path mismatches\n\n"
            "8. Commit all changes with: git add -A && git commit -m 'implement feature'\n"
            "   The FeatureBench harness extracts changes via git diff, so commits are required.\n\n"
            "Write a summary of what was implemented to .factory/reviews/builder-latest.md."
        ),
        reads={
            ".factory/strategy/current.md",
            ".factory/reviews/health-check.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
        ],
    )

    # ── Code reviewer: check against interface specs ────────────────

    nodes["code_reviewer"] = AgentNode(
        id="code_reviewer",
        role=AgentRole.CODE_REVIEWER,
        timeout=900,
        prompt_template=(
            "Review the FeatureBench implementation against interface specifications.\n\n"
            "You are running inside a FeatureBench Docker container at {project_path}.\n\n"
            "Read:\n"
            "- problem_statement.md for the authoritative interface specs\n"
            "- .factory/strategy/current.md for the implementation plan\n"
            "- .factory/reviews/builder-latest.md for what was built\n\n"
            "Check the implementation using the 7-category checklist:\n\n"
            "1. **Correctness** — Do function signatures match the interface specs exactly?\n"
            "   Are return types correct? Do all required functions/classes exist?\n\n"
            "2. **Security** — No access to /usr/local/lib/python* (gold solution).\n"
            "   No fetching from blacklisted URLs. No reading test files.\n\n"
            "3. **Edge Cases** — Are boundary conditions handled? Empty inputs, None values,\n"
            "   missing keys, type mismatches?\n\n"
            "4. **Missing Tests** — Are there obvious gaps in test coverage for the\n"
            "   implemented features? (Note: do NOT modify test files — just flag gaps.)\n\n"
            "5. **Style** — Does the code follow the repo's existing conventions?\n"
            "   Naming patterns, error handling, logging style?\n\n"
            "6. **Scope** — Does the implementation match the plan? No extra features\n"
            "   beyond what the problem statement requires?\n\n"
            "7. **Guardrails** — Anti-cheating compliance. No P2P regression risks from\n"
            "   modifying existing code paths.\n\n"
            "For each category, report PASS or FAIL with details.\n\n"
            "CRITICAL: If ANY category is FAIL with a critical issue (wrong function\n"
            "signature, missing required file, anti-cheating violation, import path\n"
            "mismatch), include the marker line 'CRITICAL_FOUND' at the top of the report.\n\n"
            "Write the full review to .factory/reviews/code-review.md."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/strategy/current.md",
        },
        writes={".factory/reviews/code-review.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/code-review.md", must_exist=True),
        ],
    )

    # ── Review gate: critical issues found? ─────────────────────────

    nodes["gate_review"] = GateNode(
        id="gate_review",
        evaluator_type="fn",
        evaluator_command=(
            "if grep -q 'CRITICAL_FOUND' "
            "{project_path}/.factory/reviews/code-review.md; "
            "then echo 'HALT'; else echo 'PROCEED'; fi"
        ),
        reads={".factory/reviews/code-review.md"},
    )

    # ── Adversarial tester: actually run/import the code ────────────

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        prompt_template=(
            "Test the FeatureBench implementation by actually running and importing the code.\n\n"
            "You are running inside a FeatureBench Docker container at {project_path}.\n\n"
            "Read:\n"
            "- problem_statement.md for interface specifications\n"
            "- .factory/strategy/current.md for the implementation plan\n"
            "- .factory/reviews/builder-latest.md for what was built\n\n"
            "For each interface endpoint specified in problem_statement.md:\n\n"
            "1. **Import test** — Try importing the module. Verify the import path matches\n"
            "   the spec exactly. Report any ImportError or ModuleNotFoundError.\n\n"
            "2. **Function/class existence** — Verify each required function/class is\n"
            "   accessible at the specified import path with the correct signature.\n\n"
            "3. **Basic invocation** — Call each function with simple valid inputs.\n"
            "   Verify it returns the expected type and doesn't crash.\n\n"
            "4. **Edge case probing** — Try boundary inputs (empty, None, large values)\n"
            "   where the spec defines behavior for these cases.\n\n"
            "5. **Anti-cheating compliance** — Verify the implementation doesn't:\n"
            "   - Read from /usr/local/lib/python* paths\n"
            "   - Access test files\n"
            "   - Fetch from blacklisted URLs\n"
            "   Check by grepping the source files for suspicious patterns.\n\n"
            "For EVERY test, produce evidence: the exact command/code run and its output.\n\n"
            "Report verdict: PASS (all endpoints work) or FAIL (issues found, with details).\n\n"
            "Write the full test report to .factory/reviews/adversarial-qa.md."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/strategy/current.md",
        },
        writes={".factory/reviews/adversarial-qa.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/adversarial-qa.md", must_exist=True),
        ],
    )

    # ── QA gate: agent-evaluated (CEO judgment) ─────────────────────

    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the QA results for this FeatureBench implementation.\n\n"
            "Read:\n"
            "- .factory/reviews/code-review.md (code reviewer findings)\n"
            "- .factory/reviews/adversarial-qa.md (adversarial test results)\n\n"
            "Determine the verdict:\n\n"
            "- PROCEED: Both code review and adversarial tests look acceptable.\n"
            "  Minor style issues or warnings are OK — the test suite will catch\n"
            "  any remaining problems. Advance to the health checker.\n\n"
            "- RELOOP: Significant issues found — wrong function signatures,\n"
            "  missing required files, import path mismatches, anti-cheating\n"
            "  violations, or functions that crash on basic inputs. Send back\n"
            "  to the builder for fixes. (Max 3 iterations before giving up.)\n\n"
            "Output exactly one line: 'PROCEED' or 'RELOOP'."
        ),
        reads={
            ".factory/reviews/adversarial-qa.md",
            ".factory/reviews/code-review.md",
        },
    )

    # ── Health checker: run F2P + P2P tests ─────────────────────────

    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        prompt_template=(
            "Run the test suite to verify the FeatureBench implementation.\n\n"
            "You are running inside a FeatureBench Docker container at {project_path}.\n"
            "The conda environment 'testbed' should be activated for running tests.\n\n"
            "1. Run the project's test suite. Look for pytest or unittest configuration\n"
            "   in the repo. Run ALL tests — both F2P (feature validation) and P2P\n"
            "   (regression) tests.\n\n"
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

    # ── Test gate: all tests pass? ──────────────────────────────────

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

    # ── Archivist: record learnings (async) ─────────────────────────

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        model="haiku",
        prompt_template=(
            "Archive learnings from this FeatureBench task.\n\n"
            "Read:\n"
            "- .factory/strategy/current.md (implementation plan)\n"
            "- .factory/reviews/builder-latest.md (what was built)\n"
            "- .factory/reviews/code-review.md (code review findings)\n"
            "- .factory/reviews/adversarial-qa.md (adversarial test results)\n"
            "- .factory/reviews/health-check.md (test results)\n\n"
            "Record:\n"
            "1. Task outcome: resolved or not, number of builder iterations needed\n"
            "2. QA loop effectiveness: did code review / adversarial testing catch\n"
            "   issues before the test suite? How many reloops were QA-driven vs test-driven?\n"
            "3. Successful strategies: what worked well\n"
            "4. Failure patterns: what caused test failures and how they were fixed\n"
            "5. Repository-specific notes: conventions, quirks, or patterns\n"
            "6. Transferable insights: patterns that would help on similar tasks\n\n"
            "Write to .factory/archive/featurebench-learnings.md."
        ),
        reads={
            ".factory/reviews/health-check.md",
            ".factory/reviews/builder-latest.md",
            ".factory/strategy/current.md",
            ".factory/reviews/code-review.md",
            ".factory/reviews/adversarial-qa.md",
        },
        writes={".factory/archive/featurebench-learnings.md"},
        blocking=False,
    )

    # ── Edges ───────────────────────────────────────────────────────

    edges = [
        Edge(source="researcher", target="strategist"),
        Edge(source="strategist", target="builder"),
        Edge(source="builder", target="code_reviewer"),
        Edge(source="code_reviewer", target="gate_review"),
        Edge(source="gate_review", target="adversarial_tester", condition=VerdictType.PROCEED),
        Edge(source="gate_review", target="health_checker", condition=VerdictType.HALT),
        Edge(source="adversarial_tester", target="gate_qa"),
        Edge(source="gate_qa", target="health_checker", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
        Edge(source="health_checker", target="gate_tests"),
        Edge(source="gate_tests", target="archivist", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ────────────────────────────────────────────────────

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
