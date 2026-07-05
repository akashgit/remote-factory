---
name: workflow-swebench
description: "SWE-bench benchmark mode — minimal 4-node pipeline for solving GitHub issues in containerized evaluation. Reads the task instruction, fixes the bug, runs tests, and merges to main. No eval infrastructure, no deep-QA, no research phases. Use when invoked with --mode swebench inside a Harbor benchmark container."
disable-model-invocation: true
argument-hint: "<project_path> --prompt /tmp/task-instruction.md"
---

# Swebench Workflow

The user wants: **$ARGUMENTS**

## Step: Study

```bash
mkdir -p $PROJECT_PATH/.factory/reviews && cd $PROJECT_PATH && (echo '=== Repository Structure ===' && find . -type f -name '*.py' | head -200 && echo '\n=== Test Files ===' && find . -type f -name 'test_*.py' -o -name '*_test.py' | head -50 && echo '\n=== Configuration Files ===' && ls -la setup.py setup.cfg pyproject.toml tox.ini conftest.py 2>/dev/null || true && echo '\n=== Task Instruction ===' && cat /tmp/task-instruction.md 2>/dev/null || echo 'No task instruction file found at /tmp/task-instruction.md') > .factory/reviews/study-output.md 2>&1
```

## Phase 1: Builder

```bash
factory agent builder --task "You are fixing a bug in an open-source project for the SWE-bench benchmark.

## Your Task

1. **Read the task instruction** — Read /tmp/task-instruction.md for the full bug description and task requirements.

2. **Understand the codebase** — explore the repository structure. Read relevant source files, test files, and configuration. Identify the root cause of the bug described in the task.

3. **Implement the fix** — make the MINIMAL change that resolves the issue. Do NOT refactor, modernize, or add unrelated improvements. Fix ONLY the described bug.

4. **Run the project's own tests** — this is CRITICAL. Run the test suite to verify your fix works AND existing tests still pass. Use pytest, tox, or whatever test runner the project uses. If specific test files are mentioned in the task, run those first.

5. **Commit your changes** — commit directly on the current branch with a descriptive message referencing the issue. Do NOT create a new branch. Do NOT create a PR.

## Rules

- MINIMAL fix only — smallest diff that resolves the issue
- MUST run tests before committing — never commit untested code
- Do NOT create branches or PRs — commit on current branch
- Do NOT run factory commands (factory eval, factory study, etc.)
- Do NOT modify test files unless the bug is IN the test infrastructure
- If tests fail after your fix, investigate and fix the issue

Read: .factory/reviews/study-output.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

### Gate — Verify (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo 'NO_COMMITS') && if [ "$CHANGES" = 'NO_COMMITS' ] || [ -z "$CHANGES" ]; then echo 'fail: builder did not commit any changes'; exit 0; fi && BUILDER_OUTPUT=$(cat .factory/reviews/builder-latest.md 2>/dev/null || echo '') && if echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(pass|succeed|ok|PASSED)'; then echo 'pass: builder reports tests passing'; elif echo "$BUILDER_OUTPUT" | grep -qiE 'tests?.*(fail|error|FAILED)'; then echo 'reloop: builder needs to retry — tests did not pass'; else echo 'pass: changes committed, no issues detected'; fi
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `auto_merge`
- **HALT** (exit non-zero / FAIL in output) → do NOT spawn `auto_merge`. Skip to the next CEO review gate or finalize as error.

*On RELOOP: return to `builder` (max 3 iterations)*

## Step: Auto Merge

```bash
cd $PROJECT_PATH && CURRENT=$(git rev-parse --abbrev-ref HEAD) && if [ "$CURRENT" = 'main' ] || [ "$CURRENT" = 'master' ]; then echo 'Already on main/master branch — no merge needed'; exit 0; fi && BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo main) && git branch -f "$BASE" HEAD && echo "Fast-forwarded $BASE to $(git rev-parse --short HEAD)"
```
