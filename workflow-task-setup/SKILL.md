---
name: workflow-task-setup
description: "Task setup mode — scaffolds Task files (.factory/tasks/<name>.toml or .py) from a target repository. A conversational wizard that studies the repo, classifies whether the task needs TOML (shell command + exit code/JSON) or Python (custom control flow), and produces a validated TaskDefinition. Use when the user says 'set up a task', 'create an evaluation harness', or wants to define what to evaluate for the outer loop."
disable-model-invocation: true
argument-hint: "<project_path> --focus 'task description'"
---

# Task Setup Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Research (Parallel)

Spawn 2 agents in parallel:

```bash
factory agent researcher --review-tag domain --task "Domain analysis for task setup. Study the target repository: language, framework, test infrastructure, CI/CD setup, and existing evaluation patterns. Identify what the project does, what its key outputs are, and how quality is currently measured (test suites, linting, benchmarks). Document: project purpose, tech stack, existing test commands, directory structure, and key source files. Write findings to .factory/strategy/research-domain.md.
Write output to: .factory/strategy/research-domain.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag verification --task "Verification method analysis for task setup. Study how the target project verifies correctness: - Does it use pytest, unittest, or another test framework? - Are there integration tests, benchmarks, or eval scripts? - Does any test output structured JSON with scores? - Is verification binary (pass/fail) or graded (partial credit)? Classify the verification type: - EXECUTABLE: shell command + exit code or JSON parse → TOML task - JUDGMENTAL: custom control flow, multi-stage, or LLM-based → Python task This classification follows the eval_spec.py classify_eval_spec_item pattern. Write findings to .factory/strategy/research-verification.md.
Write output to: .factory/strategy/research-verification.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
wait
```

**Important:** Run ALL commands above in a **single** Bash tool call with timeout set to at least 600 seconds.

```bash
# Artifact verification: researcher_domain
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-domain.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_domain: .factory/strategy/research-domain.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_domain: .factory/strategy/research-domain.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_domain" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_domain artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_domain" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_verification
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-verification.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_verification: .factory/strategy/research-verification.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_verification: .factory/strategy/research-verification.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_verification" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_verification artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_verification" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(post-barrier harness verification — DO NOT SKIP)*

## Barrier: Research

Wait for all parallel agents to complete: `researcher_domain`, `researcher_verification`

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-domain.md`, `.factory/strategy/research-verification.md`
3. Assess: Is the domain well-documented? Is the verification classification (EXECUTABLE vs JUDGMENTAL) supported by evidence from the codebase? PROCEED if both researchers produced substantive findings. RELOOP if either is shallow or missing.
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `fork_research` (max 3 iterations)*

## Phase 2: Strategist

```bash
factory agent strategist --task "Draft a TaskDefinition for this project. Read ALL research files at .factory/strategy/research-*.md. Based on the verification classification: - If EXECUTABLE: draft a TOML task definition with [task], [instances],   [setup], [prompt], [verify], [scoring], and [constraints] sections.   The verify command should be a shell command that exits 0 on success.   Choose scoring method: 'exit_code' for binary, 'json' for graded. - If JUDGMENTAL: draft a Python Task subclass skeleton with custom   instances(), setup(), prompt(), and verify() hooks. Include docstrings   explaining what each hook should do for this specific domain. Include a proposed task name (kebab-case), description, timeout, and required capabilities. Write the complete draft to .factory/strategy/current.md.
Read: .factory/strategy/research-domain.md, .factory/strategy/research-verification.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: strategist
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/current.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: strategist: .factory/strategy/current.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: strategist: .factory/strategy/current.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=strategist" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: strategist artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=strategist" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Steering Point — Strategy (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input

*On RELOOP: return to `strategist` (max 3 iterations)*

## Phase 3: Builder

```bash
factory agent builder --task "Write the task file from the approved specification. Read the approved spec at .factory/strategy/current.md. If the spec describes a TOML task: write .factory/tasks/<name>.toml with all required sections. If the spec describes a Python task: write .factory/tasks/<name>.py with a Task subclass implementing the four hooks. Ensure the task directory exists (mkdir -p .factory/tasks/). After writing, run: factory task validate <name> to verify the task definition is valid.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: builder
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/builder-latest.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: builder artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 4: Qa (Parallel)

Spawn 3 agents in parallel:

```bash
factory agent health_checker --task "Execute health_checker task for the project.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md
Write output to: .factory/reviews/health-check.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent code_reviewer --task "Execute code_reviewer task for the project.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md
Write output to: .factory/reviews/code-review.md" --project "$PROJECT_PATH" --timeout 900 &
```

```bash
factory agent adversarial_tester --task "Execute adversarial_tester task for the project.
Read: .factory/reviews/builder-latest.md, .factory/strategy/current.md
Write output to: .factory/reviews/adversarial-qa.md" --project "$PROJECT_PATH" --timeout 1800 &
```

```bash
wait
```

**Important:** Run ALL commands above in a **single** Bash tool call with timeout set to at least 1800 seconds.

```bash
# Artifact verification: health_checker
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/health-check.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: health_checker artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: code_reviewer
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/code-review.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: code_reviewer artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: adversarial_tester
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/adversarial-qa.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: adversarial_tester: .factory/reviews/adversarial-qa.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: adversarial_tester: .factory/reviews/adversarial-qa.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=adversarial_tester" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: adversarial_tester artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=adversarial_tester" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(post-barrier harness verification — DO NOT SKIP)*

## Barrier: Qa

Wait for all parallel agents to complete: `health_checker`, `code_reviewer`, `adversarial_tester`

Read combined outputs: `.factory/reviews/adversarial-qa.md`, `.factory/reviews/code-review.md`, `.factory/reviews/health-check.md`

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/adversarial-qa.md`, `.factory/reviews/code-review.md`, `.factory/reviews/health-check.md`
3. Assess: Review QA results for the task definition. PROCEED if all checks pass. RELOOP to builder (max 3 iterations) if issues found.
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Step: Validate Task

Hard validation gate — the task must pass all checks. The {task_name} placeholder is replaced by the CEO with the actual task name from the builder output.

```bash
factory task validate {task_name}
```

## Phase 5: Archivist

```bash
factory agent archivist --task "Archive the task setup results and task definition.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/archive/task-setup.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
