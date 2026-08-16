"""DevOps Gym benchmark workflow — lean pipeline for build/configuration tasks.

4-node pipeline: study -> solver -> gate_verify -> auto_merge
RELOOP from gate_verify back to solver (max 3 iterations) on failure.

Designed for Harbor containers where:
- Task instruction is at /tmp/task-instruction.md
- Targets DevOps build/configuration: Maven, Gradle, Go modules, Make, Docker, CI/CD
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
    "name": "devopsgym",
    "description": (
        "DevOps Gym benchmark mode — 4-node pipeline for solving "
        "build/configuration tasks (Maven, Gradle, Go modules, Make, Docker, CI/CD). "
        "study -> solver -> gate_verify -> auto_merge with RELOOP on failure."
    ),
}


def workflow() -> Workflow:
    """Build the DevOps Gym workflow as a lean 4-node pipeline."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # -- Node 1: Study --
    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Workspace ===' && "
            "ls -la && "
            "echo '\\n=== Build Files ===' && "
            "find . -type f \\( "
            "-name 'pom.xml' -o -name 'build.gradle' -o -name 'build.gradle.kts' "
            "-o -name 'go.mod' -o -name 'go.sum' "
            "-o -name 'Makefile' -o -name 'CMakeLists.txt' "
            "-o -name 'Dockerfile' -o -name 'docker-compose.yml' -o -name 'docker-compose.yaml' "
            "-o -name 'Jenkinsfile' -o -name 'Cargo.toml' "
            "-o -name 'package.json' -o -name 'requirements.txt' -o -name 'setup.py' "
            "\\) | head -100 && "
            "echo '\\n=== CI/CD Config ===' && "
            "find . -type f \\( "
            "-name '*.yml' -o -name '*.yaml' "
            "\\) -path '*/.github/workflows/*' | head -50 && "
            "find . -type f -name '.gitlab-ci.yml' | head -10 && "
            "echo '\\n=== Source Files ===' && "
            "find . -type f \\( "
            "-name '*.java' -o -name '*.go' -o -name '*.py' "
            "-o -name '*.rs' -o -name '*.c' -o -name '*.cpp' "
            "-o -name '*.sh' -o -name '*.bash' "
            "\\) | head -100 && "
            "echo '\\n=== Git ===' && "
            "git status 2>/dev/null || echo 'Not a git repository' && "
            "git log --oneline -10 2>/dev/null || true && "
            "echo '\\n=== Task ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction found at /tmp/task-instruction.md' && "
            "echo '\\n=== Build System Detection ===' && "
            "echo 'Attempting to identify and run build...' && "
            "([ -f pom.xml ] && echo 'Detected: Maven' && mvn --version 2>/dev/null || true) && "
            "([ -f build.gradle ] || [ -f build.gradle.kts ] && echo 'Detected: Gradle' && gradle --version 2>/dev/null || true) && "
            "([ -f go.mod ] && echo 'Detected: Go modules' && go version 2>/dev/null || true) && "
            "([ -f Makefile ] && echo 'Detected: Make' || true) && "
            "([ -f Dockerfile ] && echo 'Detected: Docker' || true)"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    # -- Node 2: Solver (Builder) --
    nodes["solver"] = AgentNode(
        id="solver",
        role=AgentRole.BUILDER,
        model="opus",
        timeout=7200,
        max_iterations=3,
        prompt_template=(
            "You are solving a DevOps build/configuration task from the DevOps Gym benchmark.\n\n"
            "## Your Task\n\n"
            "1. **Read the task instruction** — Read /tmp/task-instruction.md carefully. "
            "Understand exactly what build or configuration issue needs to be fixed and "
            "what the expected behavior should be.\n\n"
            "2. **Understand the project** — Check the study output at "
            ".factory/reviews/study-output.md for a structural overview. Examine build "
            "files (pom.xml, build.gradle, go.mod, Makefile, Dockerfile, CI/CD configs), "
            "source files, and any error logs.\n\n"
            "3. **Analyze the build system** — Identify which build system is in use "
            "(Maven, Gradle, Go modules, Make, Docker, etc.). Understand the project's "
            "dependency structure, build targets, and configuration.\n\n"
            "4. **Fix the issue** — Implement the fix described in the task instruction. "
            "This may involve modifying build configuration, fixing dependency declarations, "
            "updating CI/CD pipelines, fixing Dockerfiles, or adjusting build scripts.\n\n"
            "5. **Verify the fix** — Attempt to build the project using the appropriate "
            "build tool. Verify the build succeeds and the configuration is correct.\n\n"
            "6. **Commit your changes** — Commit directly on the current branch "
            "with a descriptive message. Do NOT create a new branch. Do NOT create a PR.\n\n"
            "## Rules\n\n"
            "- Act AUTONOMOUSLY — do NOT ask for confirmation or input\n"
            "- PRESERVE the existing build system — do NOT switch build tools or "
            "modernize the build configuration unless explicitly asked. Fix ONLY the "
            "specific issue described in the task instruction.\n"
            "- HIDDEN TESTS: The benchmark uses hidden verification steps. Do NOT "
            "hardcode outputs. Implement the general fix that solves the problem "
            "for any valid build configuration.\n"
            "- Do NOT create branches or PRs — commit on current branch\n"
            "- Do NOT run factory commands (factory eval, factory study, etc.)\n"
            "- If something fails, investigate root cause and try alternative approaches\n"
        ),
        reads={".factory/reviews/study-output.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    # -- Node 3: Gate Verify --
    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
            "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
            "echo 'fail: solver did not commit any changes'; "
            "exit 0; fi && "
            "if [ ! -f .factory/reviews/builder-latest.md ]; then "
            "echo 'fail: solver output missing'; "
            "exit 0; fi && "
            "BUILD_OK=0 && "
            "if [ -f pom.xml ]; then "
            "timeout 600 mvn compile -q 2>&1 && BUILD_OK=1 || "
            "{ TAIL=$(timeout 600 mvn compile 2>&1 | tail -50); "
            "echo \"reloop: Maven build failed — $TAIL\"; exit 0; }; fi && "
            "if [ -f build.gradle ] || [ -f build.gradle.kts ]; then "
            "timeout 600 gradle build -q 2>&1 && BUILD_OK=1 || "
            "{ TAIL=$(timeout 600 gradle build 2>&1 | tail -50); "
            "echo \"reloop: Gradle build failed — $TAIL\"; exit 0; }; fi && "
            "if [ -f go.mod ]; then "
            "timeout 600 go build ./... 2>&1 && BUILD_OK=1 || "
            "{ TAIL=$(timeout 600 go build ./... 2>&1 | tail -50); "
            "echo \"reloop: Go build failed — $TAIL\"; exit 0; }; fi && "
            "if [ -f Makefile ]; then "
            "timeout 600 make 2>&1 && BUILD_OK=1 || "
            "{ TAIL=$(timeout 600 make 2>&1 | tail -50); "
            "echo \"reloop: Make build failed — $TAIL\"; exit 0; }; fi && "
            "if [ $BUILD_OK -eq 0 ]; then "
            "echo 'pass: no recognized build system — deferring to Harbor verifier'; "
            "exit 0; fi && "
            "echo 'pass: build succeeded'"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # -- Node 4: Auto Merge --
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

    # -- Edges --
    edges = [
        Edge(source="study", target="solver"),
        Edge(source="solver", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="solver", condition=VerdictType.RELOOP),
    ]

    # -- Trigger --
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "devopsgym"

    return Workflow(
        name="devopsgym",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
