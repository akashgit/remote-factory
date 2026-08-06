---
name: workflow-mini-swebench
description: "Run the mini-swebench workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Mini Swebench Workflow

The user wants: **$ARGUMENTS**

## Step: Study

```bash
mkdir -p $PROJECT_PATH/.factory/reviews && cd $PROJECT_PATH && (echo '=== Repository Structure ===' && find . -type f -name '*.py' | head -200 && echo '\n=== Test Files ===' && find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && echo '\n=== Configuration Files ===' && ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && echo '\n=== Task Instruction ===' && cat /tmp/task-instruction.md 2>/dev/null || echo 'No task instruction file found at /tmp/task-instruction.md') > .factory/reviews/study-output.md 2>&1
```

## Phase 1: Builder — Solver

```bash
factory agent builder --task "You are a helpful assistant that can interact with a computer shell to solve programming tasks.

<instructions>
# Task Instructions

## Overview

You're a software engineer interacting continuously with a computer by submitting commands.
You'll be helping implement necessary changes to meet requirements in the task instruction.
Your task is specifically to make changes to non-test files in the current directory in order to fix the issue described in the task instruction in a way that is general and consistent with the codebase.
This is an interactive process where you will think and issue AT LEAST ONE command, see the result, then think and issue your next command(s).

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
- Your response MUST include AT LEAST ONE bash tool call. You can make MULTIPLE tool calls in a single response when the commands are independent (e.g., searching multiple files, reading different parts of the codebase).
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files

## Environment Details

- You have a full Linux shell environment
- Always use non-interactive flags (-y, -f) for commands
- Avoid interactive tools like vi, nano, or any that require user input
- You can use bash commands or invoke any tool that is available in the environment
- You can also create new tools or scripts to help you with the task
- If a tool isn't available, you can also install it

## Submission

When you've completed your work, commit your changes directly on the current branch.

Step 1: Stage only the source files you modified
Run `git add path/to/file1 path/to/file2` listing only the source files you modified.
Do NOT stage test files, reproduction scripts, or configuration files.

Step 2: Commit with a descriptive message
Run `git commit -m "Fix: <brief description of the fix>"`.

Do NOT create branches or PRs. Commit directly on the current branch.
Do NOT leave temporary test or reproduction scripts in the repo — remove them before committing.
</instructions>

Read: .factory/reviews/study-output.md
Write output to: .factory/reviews/builder-latest.md
Read: .factory/reviews/study-output.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 7200
```

### Gate — Verify (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && if [ "$CHANGES" = 'NO_COMMITS' ] || [ -z "$CHANGES" ]; then echo 'fail: solver did not commit any changes'; exit 0; fi && BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && if echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then echo 'pass: solver reports tests passing'; elif echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(fail|error|FAILED)'; then echo 'reloop: solver needs to retry — tests did not pass'; else echo 'pass: changes committed, no issues detected'; fi
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `auto_merge`
- **HALT** (exit non-zero / FAIL in output) → do NOT spawn `auto_merge`. Skip to the next CEO review gate or finalize as error.

*On RELOOP: return to `solver` (max 3 iterations)*

## Step: Auto Merge

```bash
cd $PROJECT_PATH && CURRENT=$(git rev-parse --abbrev-ref HEAD) && COMMON=$(git rev-parse --git-common-dir) && BASE=$(git --git-dir="$COMMON" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main) && if [ "$CURRENT" = "$BASE" ]; then echo "Already on $BASE — no merge needed"; exit 0; fi && git update-ref refs/heads/"$BASE" HEAD && PARENT_WT=$(cd "$COMMON/.." && pwd) && git diff-tree --no-commit-id --name-only -r HEAD HEAD~1 | while read file; do if [ -f "$file" ]; then mkdir -p "$PARENT_WT/$(dirname $file)" && cp "$file" "$PARENT_WT/$file"; fi; done && echo "Updated $BASE to $(git rev-parse --short HEAD)"
```
