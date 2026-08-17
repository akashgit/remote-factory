"""FeatureBench benchmark workflow — feature implementation pipeline for containerized evaluation.

5-node pipeline: study → builder → gate_tests → auto_merge
RELOOP from gate_tests through health_checker (agent diagnostic) back to builder.

    study → builder → gate_tests --PROCEED--> auto_merge
                           |
                           +--RELOOP--> health_checker (agent) → builder

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md (detailed problem statement with
  explicit interface definitions: function signatures, import paths, types)
- Solutions must be directly callable modules matching the specified interface exactly
- Evaluation uses fail-to-pass + pass-to-pass tests — ALL must pass for 'resolved'
- Harbor's verifier is the FINAL authority on pass/fail
- Harbor checks the MAIN branch for changes
- No .factory/ infrastructure (no eval, no experiments, no deep-QA)
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "featurebench",
    "description": (
        "FeatureBench benchmark mode — 5-node pipeline for implementing "
        "new features in Python codebases with explicit interface specs. "
        "study → builder → gate_tests → auto_merge with RELOOP through "
        "health_checker (agent diagnostic) back to builder on test failure."
    ),
}


def workflow() -> Workflow:
    """Build the FeatureBench workflow from scratch (not composed from improve)."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Node 1: Study ──────────────────────────────────────────────
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Repository Structure ===' && "
            "find . -type f -name '*.py' | head -200 && "
            "echo '\\n=== Package Layout ===' && "
            "find . -type d -name '__pycache__' -prune -o -type d -print | head -50 && "
            "echo '\\n=== Test Files ===' && "
            "find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && "
            "echo '\\n=== Configuration Files ===' && "
            "ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && "
            "echo '\\n=== Placeholder Implementations ===' && "
            "grep -rl 'NotImplementedError\\|^\\s*pass$' --include='*.py' . 2>/dev/null | head -50 || true && "
            "echo '\\n=== Task Instruction ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction file found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # ── Node 2: Builder ────────────────────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=7200,
        max_iterations=3,
        prompt_template=(
            "You are implementing a new feature in a Python codebase for "
            "the FeatureBench benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the FULL task description** — Read /tmp/task-instruction.md "
            "carefully. It contains detailed interface specifications: function "
            "signatures, import paths, input/output types, and expected behavior. "
            "These specs are the contract your code must satisfy.\n\n"
            "2. **Understand the existing codebase** — Explore the repository "
            "structure thoroughly. Read related source files, understand module "
            "layout, imports, and existing patterns. Check the study output at "
            ".factory/reviews/study-output.md for a structural overview.\n\n"
            "3. **CRITICAL: Read before you write** — Before implementing ANY "
            "function, navigate to and READ the actual source code for every "
            "function, class, or module you reference. DO NOT guess function "
            "signatures, import paths, or class attributes. The most common "
            "failure mode is agents hallucinating interfaces instead of reading "
            "the actual code — NameError and ImportError from wrong cross-file "
            "references.\n\n"
            "4. **Implement the feature** — Follow the specified interfaces "
            "EXACTLY: match function names, parameter names, types, return types, "
            "and import paths precisely. The evaluation checks that your code is "
            "directly callable via the specified interface.\n\n"
            "5. **Handle cross-file dependencies** — If the feature spans multiple "
            "files, ensure ALL imports and references resolve correctly. Check "
            "that every module you import exists, every function you call is "
            "defined, and every class attribute you access is real.\n\n"
            "6. **Run the project's test suite** — Execute the tests to verify "
            "your implementation. Look specifically for NameError, ImportError, "
            "and TypeError in test output — these are signals of missing cross-file "
            "connections or interface mismatches.\n\n"
            "7. **Read diagnostic feedback** — If the file "
            ".factory/reviews/health-check.md exists, read it carefully. It "
            "contains parsed test failure analysis from the health checker agent "
            "on a previous iteration: stack traces, root causes, and suggested "
            "fixes. Use this feedback to guide your changes.\n\n"
            "8. **Iterate on test failures** — If tests fail, trace the error "
            "to its root cause. Fix missing dependencies, correct interface "
            "mismatches, and re-run until tests pass.\n\n"
            "9. **Commit your changes** — Commit directly on the current branch "
            "with a descriptive message. Do NOT create a new branch. Do NOT "
            "create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- Follow interface specs EXACTLY — the evaluation checks that your "
            "code is directly callable via the specified signatures and import paths\n"
            "- Do NOT modify test files\n"
            "- Do NOT guess — READ the actual source code for any function/class "
            "you reference\n"
            "- If tests fail with NameError or ImportError, trace the missing "
            "dependency and fix it\n"
            "- If tests fail with TypeError, check that your function signatures "
            "match the specs exactly\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
        ),
        reads={".factory/reviews/study-output.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # ── Node 3: Gate Tests ───────────────────────────────────────
    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "mkdir -p .factory/reviews && "
            "TEST_FILES=$(find . -name 'test_*.py' -o -name '*_test.py' 2>/dev/null | head -1) && "
            "if [ -n \"$TEST_FILES\" ]; then "
            "conda run -n testbed pytest /testbed -x --tb=short "
            "> .factory/reviews/gate-pytest-output.txt 2>&1; "
            "RC=$?; "
            "FAIL_COUNT=$(grep -cE '^FAILED ' .factory/reviews/gate-pytest-output.txt "
            "2>/dev/null || echo 0); "
            "PREV_COUNT=$(cat .factory/reviews/gate-prev-fail-count.txt 2>/dev/null "
            "|| echo -1); "
            "echo \"$FAIL_COUNT\" > .factory/reviews/gate-prev-fail-count.txt; "
            "if [ $RC -eq 0 ]; then "
            "echo 'pass: all tests passed'; "
            "elif [ \"$PREV_COUNT\" != \"-1\" ] && [ \"$FAIL_COUNT\" -gt \"$PREV_COUNT\" ]; then "
            "echo 'fail: regression detected — iteration has more failures than previous "
            "('\"$FAIL_COUNT\"' > '\"$PREV_COUNT\"')'; "
            "else "
            "echo 'reloop: pytest failed ('\"$FAIL_COUNT\"' failures) — "
            "see .factory/reviews/gate-pytest-output.txt'; "
            "fi; "
            "else "
            "echo 'pass: no test files found'; "
            "fi"
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/reviews/gate-pytest-output.txt"},
    )

    # ── Node 4: Health Checker (RELOOP diagnostic) ────────────────
    nodes["health_checker"] = AgentNode(
        id="health_checker",
        role=AgentRole.HEALTH_CHECKER,
        model="opus",
        timeout=600,
        max_iterations=3,
        prompt_template=(
            "You are a diagnostic analyst for the FeatureBench benchmark.\n\n"
            "## Your Task\n\n"
            "The test gate has detected failures. Your job is to analyze the "
            "pytest output and provide actionable diagnostic feedback for the "
            "builder agent.\n\n"
            "1. **Read the pytest output** at .factory/reviews/gate-pytest-output.txt. "
            "If this file does not exist, this is an L2 task with no tests. In that "
            "case, read the task instruction at /tmp/task-instruction.md and the "
            "builder output at .factory/reviews/builder-latest.md, perform spec "
            "compliance checks, and write 'SPEC_COMPLIANCE: PASS' or "
            "'SPEC_COMPLIANCE: FAIL' at the top of your output.\n\n"
            "2. **Parse every failure** — For each FAILED test, extract:\n"
            "   - The full test name\n"
            "   - The stack trace\n"
            "   - The error type (NameError, ImportError, TypeError, etc.)\n\n"
            "3. **Root cause analysis** — For each failure, determine:\n"
            "   - What went wrong (missing import, wrong signature, etc.)\n"
            "   - Which source file and line caused the error\n"
            "   - The specific fix needed\n\n"
            "4. **Write diagnostic report** — Write a structured report to "
            ".factory/reviews/health-check.md with:\n"
            "   - Summary of failure count and error types\n"
            "   - Per-failure analysis with root cause and suggested fix\n"
            "   - Priority order: fix the failures most likely to unblock others first\n\n"
            "## Rules\n\n"
            "- DO NOT run pytest yourself. The gate already ran it.\n"
            "- DO NOT modify any source files\n"
            "- DO NOT attempt to fix the code — only diagnose\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation\n"
        ),
        reads={
            ".factory/reviews/gate-pytest-output.txt",
            ".factory/reviews/builder-latest.md",
        },
        writes={".factory/reviews/health-check.md"},
    )

    # ── Node 5: Auto Merge ─────────────────────────────────────────
    nodes["auto_merge"] = FnNode(
        id="auto_merge",
        command=(
            "cd {project_path} && "
            "CURRENT=$(git rev-parse --abbrev-ref HEAD) && "
            "COMMON=$(git rev-parse --git-common-dir) && "
            "BASE=$(git --git-dir=\"$COMMON\" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main) && "
            "if [ \"$CURRENT\" = \"$BASE\" ]; then "
            "echo \"Already on $BASE — no merge needed\"; "
            "exit 0; fi && "
            "git update-ref refs/heads/\"$BASE\" HEAD && "
            "PARENT_WT=$(cd \"$COMMON/..\" && pwd) && "
            "git diff-tree --no-commit-id --name-only -r HEAD HEAD~1 | "
            "while read file; do "
            "if [ -f \"$file\" ]; then "
            "mkdir -p \"$PARENT_WT/$(dirname $file)\" && "
            "cp \"$file\" \"$PARENT_WT/$file\"; "
            "fi; done && "
            "echo \"Updated $BASE to $(git rev-parse --short HEAD)\""
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Edges ──────────────────────────────────────────────────────

    edges = [
        Edge(source="study", target="builder"),
        Edge(source="builder", target="gate_tests"),
        Edge(source="gate_tests", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="health_checker", condition=VerdictType.RELOOP),
        Edge(source="health_checker", target="builder"),
    ]

    # ── Trigger ────────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "featurebench"

    return Workflow(
        name="featurebench",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
