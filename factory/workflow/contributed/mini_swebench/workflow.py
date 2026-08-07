"""mini-SWE-bench workflow — bash-only solver via direct LLM API calls.

4-node pipeline: study → solver → gate_verify → auto_merge
The solver node uses LLMNode (direct Anthropic API) with a single bash tool,
replicating mini-SWE-agent's architecture without Claude Code overhead.

Prompt override: set FACTORY_WORKFLOW_YAML_B64 env var with base64-encoded
YAML annotations to override slot values (prompt, timeout, etc.) at runtime.
"""

import os
from typing import Any

from factory.models import ProjectState
from factory.workflow.llm_tools import BASH_TOOL
from factory.workflow.primitives import (
    Edge,
    FnNode,
    GateNode,
    LLMNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "mini-swebench",
    "description": (
        "mini-SWE-agent style SWE-bench solver — direct LLM API calls with "
        "bash-only tool use. study → solver (LLMNode) → gate_verify → auto_merge."
    ),
}

_SYSTEM_PROMPT = (
    "You are a helpful assistant that can interact with a computer shell "
    "to solve programming tasks."
)

_INSTANCE_PROMPT = """\
<pr_description>
Consider the following PR description:

{instance_context}
</pr_description>

<instructions>
# Task Instructions

## Overview

You're a software engineer interacting continuously with a computer by submitting commands.
You'll be helping implement necessary changes to meet requirements in the PR description.
Your task is specifically to make changes to non-test files in the current directory in order \
to fix the issue described in the PR description in a way that is general and consistent with the codebase.
<IMPORTANT>This is an interactive process where you will think and issue AT LEAST ONE command, see the result, \
then think and issue your next command(s).</IMPORTANT>

For each response:

1. Include a THOUGHT section explaining your reasoning and what you're trying to accomplish
2. Provide one or more bash tool calls to execute

## Important Boundaries

- MODIFY: Regular source code files in /testbed (this is the working directory for all your subsequent commands)
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Recommended Workflow

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust

## Command Execution Rules

You are operating in an environment where

1. You issue at least one command
2. The system executes the command(s) in a subshell
3. You see the result(s)
4. You write your next command(s)

Each response should include:

1. **Reasoning text** where you explain your analysis and plan
2. At least one tool call with your command

**CRITICAL REQUIREMENTS:**

- Your response SHOULD include reasoning text explaining what you're doing
- Your response MUST include AT LEAST ONE bash tool call. You can make MULTIPLE tool calls in a \
single response when the commands are independent (e.g., searching multiple files, reading different \
parts of the codebase).
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or \
write/load environment variables from files

Example of a CORRECT response:
<example_response>
I need to understand the Builder-related code. Let me find relevant files and check the project structure.

[Makes multiple bash tool calls: {"command": "ls -la"}, {"command": "find src -name '*.java' | grep -i builder"}, {"command": "cat README.md | head -50"}]
</example_response>

## Environment Details

- You have a full Linux shell environment
- Always use non-interactive flags (-y, -f) for commands
- Avoid interactive tools like vi, nano, or any that require user input
- You can use bash commands or invoke any tool that is available in the environment
- You can also create new tools or scripts to help you with the task
- If a tool isn't available, you can also install it

## Submission

When you've completed your work, commit your changes directly on the current branch.
Follow these steps IN ORDER, with SEPARATE commands:

Step 1: Stage only the source files you modified
Run `git add path/to/file1 path/to/file2` listing only the source files you modified.

<IMPORTANT>
Only stage the specific source files you modified to fix the issue.
Do not stage any of the following files:

- test and reproduction files
- helper scripts, tests, or tools that you created
- installation, build, packaging, configuration, or setup scripts unless they are directly part of the issue you were fixing
- binary or compiled files
</IMPORTANT>

Step 2: Verify your staged changes
Run `git diff --cached` to confirm only your intended changes are staged.

Step 3: Commit with a descriptive message
Run `git commit -m "Fix: <brief description of the fix>"`.

<CRITICAL>
- Do NOT create branches or PRs — commit directly on the current branch.
- Clean up any temporary test or reproduction scripts before committing — do NOT leave them in the repo.
- You CANNOT continue working after committing.
</CRITICAL>
</instructions>"""


def _resolve_model() -> str:
    return os.environ.get("FACTORY_STUDENT_MODEL", "opus")


def _resolve_provider() -> str:
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"):
        return "vertex"
    return "anthropic"


def workflow() -> Workflow:
    """Build the mini-SWE-bench workflow with LLMNode solver."""
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    nodes["study"] = FnNode(
        id="study",
        command=(
            "mkdir -p {project_path}/.factory/reviews && "
            "cd {project_path} && "
            "("
            "echo '=== Repository Structure ===' && "
            "find . -type f -name '*.py' | head -200 && "
            "echo '\\n=== Test Files ===' && "
            "find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && "
            "echo '\\n=== Configuration Files ===' && "
            "ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && "
            "echo '\\n=== Task Instruction ===' && "
            "cat /tmp/task-instruction.md 2>/dev/null || "
            "echo 'No task instruction file found at /tmp/task-instruction.md'"
            ") > .factory/reviews/study-output.md 2>&1"
        ),
        writes={".factory/reviews/study-output.md"},
    )

    nodes["solver"] = LLMNode(
        id="solver",
        system_prompt=_SYSTEM_PROMPT,
        instance_prompt=_INSTANCE_PROMPT,
        model=_resolve_model(),
        provider=_resolve_provider(),
        tools=[BASH_TOOL],
        max_turns=100,
        max_tokens=8192,
        timeout=7200,
        reads={".factory/reviews/study-output.md"},
        writes={".factory/reviews/builder-latest.md"},
    )

    nodes["gate_verify"] = GateNode(
        id="gate_verify",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && "
            "if [ \"$CHANGES\" = 'NO_COMMITS' ] || [ -z \"$CHANGES\" ]; then "
            "echo 'fail: solver did not commit any changes'; "
            "exit 0; fi && "
            "BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && "
            "if echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then "
            "echo 'pass: solver reports tests passing'; "
            "elif echo \"$BUILDER_OUTPUT\" | grep -qiE 'tests?.*(fail|error|FAILED)'; then "
            "echo 'reloop: solver needs to retry — tests did not pass'; "
            "else "
            "echo 'pass: changes committed, no issues detected'; "
            "fi"
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

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

    edges = [
        Edge(source="study", target="solver"),
        Edge(source="solver", target="gate_verify"),
        Edge(source="gate_verify", target="auto_merge", condition=VerdictType.PROCEED),
        Edge(source="gate_verify", target="solver", condition=VerdictType.RELOOP),
    ]

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "mini-swebench"

    return Workflow(
        name="mini-swebench",
        nodes=nodes,
        edges=edges,
        start_node="study",
        terminal=True,
        trigger=trigger,
    )
