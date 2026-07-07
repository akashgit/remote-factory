"""FeatureBench benchmark workflow — feature implementation pipeline for containerized evaluation.

4-node pipeline: study → builder → gate_verify → auto_merge
RELOOP from gate_verify back to builder (max 3 iterations) on test failure.

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
        "FeatureBench benchmark mode — 4-node pipeline for implementing "
        "new features in Python codebases with explicit interface specs. "
        "study → builder → gate_verify → auto_merge with RELOOP on test failure."
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
        timeout=1200,
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
            "7. **Iterate on test failures** — If tests fail, trace the error "
            "to its root cause. Fix missing dependencies, correct interface "
            "mismatches, and re-run until tests pass.\n\n"
            "8. **Commit your changes** — Commit directly on the current branch "
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

    # ── Node 3: Gate Verify ────────────────────────────────────────
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
            "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
            "echo 'fail: builder did not commit any changes'; "
            "exit 0; fi && "
            "BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && "
            "if echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then "
            "echo 'pass: builder reports tests passing'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(fail|error|FAILED)'; then "
            "echo 'reloop: builder needs to retry — tests did not pass'; "
            "else "
            "echo 'pass: changes committed, no issues detected'; "
            "fi"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 4: Auto Merge ─────────────────────────────────────────
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
        Edge(source="builder", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="builder", condition=VerdictType.RELOOP),
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
