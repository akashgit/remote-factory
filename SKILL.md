---
name: factory
description: Autonomous multi-agent software evolution loop. Detects project state and routes to the appropriate mode — Build (scaffold via delegate), Discover (auto-detect evals), Review (human gate on evals), or Improve (agent-orchestrated observe-hypothesize-execute loop with guard rails).
user_invocable: true
---

# Factory Skill v2

Multi-agent software evolution loop for any project. Detects project state and routes to the correct mode: **Build**, **Discover**, **Review**, or **Improve**. Coordinates 6 specialized agents via GitHub.

TRIGGER when: user says /factory, or the session is started with a factory prompt.

---

## Setup

Before doing anything, determine the project and its state.

### Step 1: Identify the Project Path

```bash
# Use the current working directory, or the path the user provided
PROJECT_PATH="$(pwd)"
```

### Step 2: Detect Project State

```bash
cd ~/factory-projects/remote-factory
source .venv/bin/activate
uv run python -m factory detect "$PROJECT_PATH"
```

This prints one of five states:

| State                   | Meaning                                          | Route to       |
|-------------------------|--------------------------------------------------|----------------|
| `no_repo`               | No git repo at path                              | Build mode     |
| `incomplete`            | Repo exists, open plan/implementation issues     | Build mode     |
| `evals_pending_review`  | Eval profile exists, not yet human-reviewed      | Review mode    |
| `has_factory`           | Factory fully initialized, evals reviewed        | Improve mode   |
| `no_factory`            | Repo exists, no factory setup                    | Discover mode  |

### Step 3: Route to Mode

- `no_repo` or `incomplete` --> **Build mode**
- `no_factory` --> **Discover mode**
- `evals_pending_review` --> **Review mode**
- `has_factory` --> **Improve mode**

---

## Obsidian Integration

The factory uses an Obsidian vault named "factory" as its institutional memory. Agents can interact with it using:

### obsidian-cli commands
- `obsidian create vault="factory" name="path/to/note" content="..." silent` -- create a note
- `obsidian read vault="factory" file="note name"` -- read a note
- `obsidian search vault="factory" query="search term" limit=10` -- search the vault
- `obsidian append vault="factory" file="note name" content="..."` -- append to a note
- `obsidian property:set vault="factory" name="status" value="done" file="note name"` -- set a property

### Vault structure
```
~/factory-vault/
├── 00-Factory/          # Cross-project knowledge (Dashboard, Patterns, Decisions)
├── 10-Projects/{name}/  # Per-project notes (Experiments, Strategies, Decisions)
├── 20-Knowledge/        # Concepts and external Sources
├── _templates/          # Note templates
└── MEMORY.md            # Thin pointer index for agent orientation
```

### Syntax (obsidian-markdown)
- Wikilinks: `[[note name]]`, `[[note name|display text]]`, `[[note#heading]]`
- Embeds: `![[note]]`, `![[image.png|300]]`
- Callouts: `> [!tip] Title` (types: note, info, tip, warning, danger, example, quote)
- Tags: `#factory`, `#experiment`, `#strategy`
- Properties: YAML frontmatter between `---` markers

---

## Mode: Build (`no_repo` / `incomplete`)

The project either doesn't exist or has open plan/implementation issues. Invoke the delegate skill to scaffold or continue building.

### Steps

1. **Invoke the delegate skill** to handle the full 6-phase workflow (Plan, Spec, Review, Breakdown, Build, Finalize):

```bash
claude -p "$(cat <<'PROMPT'
You are Akash's delegate. Load your persona from ~/.claude/skills/delegate/persona.md
and execute the delegate workflow from ~/.claude/skills/delegate/SKILL.md.

Target project: $PROJECT_PATH

If the project has no repo (no_repo state), create the repo and plan the MVP.
If the project is incomplete (incomplete state), pick up the next open issue and continue building.
PROMPT
)" --dangerously-skip-permissions
```

2. **After the delegate finishes**, re-run state detection:

```bash
uv run python -m factory detect "$PROJECT_PATH"
```

3. If the state has advanced to `no_factory`, continue to **Discover mode**. If still `incomplete`, the delegate left work for Akash -- stop and report status.

---

## Mode: Discover (`no_factory`)

The repo exists but the factory hasn't been set up. Auto-discover eval dimensions and generate the eval harness.

### Step 1: Run Discovery

```bash
uv run python -m factory discover "$PROJECT_PATH"
```

This introspects the project (language, framework, project type, test/lint/type-check commands) and:
- Creates `.factory/eval_profile.json` with discovered eval dimensions
- Generates `eval/score.py` wrapping those dimensions

### Step 2: Verify Discovery Output

Read the generated profile and check it makes sense:

```bash
cat "$PROJECT_PATH/.factory/eval_profile.json"
cat "$PROJECT_PATH/eval/score.py"
```

### Step 3: Re-detect State

```bash
uv run python -m factory detect "$PROJECT_PATH"
```

State should now be `evals_pending_review`. Continue to **Review mode**.

---

## Mode: Review (`evals_pending_review`)

Eval dimensions have been auto-discovered but not yet reviewed by a human. This is the gate that prevents untrusted auto-generated evals from driving the improvement loop.

### Step 1: Present Eval Profile

Read and display the eval profile for review:

```bash
cat "$PROJECT_PATH/.factory/eval_profile.json"
```

Present to the user:
- Each eval dimension (name, command, weight, source)
- The tier (discovered/researched/fallback) and confidence
- Any concerns (e.g., tools not installed, commands that might fail)

### Step 2: Test Each Eval Dimension

Run eval/score.py to verify all dimensions actually work:

```bash
cd "$PROJECT_PATH" && python eval/score.py
```

If any dimension fails (e.g., missing tool), flag it. Options:
- Install the missing tool (add to dev deps)
- Remove the dimension from the profile
- Adjust the command

### Step 3: Human Approval

After the user approves (or adjusts) the eval profile, mark it as reviewed:

```python
import json
from pathlib import Path

profile_path = Path("$PROJECT_PATH") / ".factory" / "eval_profile.json"
profile = json.loads(profile_path.read_text())
profile["human_reviewed"] = True
profile_path.write_text(json.dumps(profile, indent=2))
```

### Step 4: Create `factory.md`

Copy the template and fill it in based on the project:

```bash
cp ~/factory-projects/remote-factory/templates/factory_config.md "$PROJECT_PATH/factory.md"
```

Edit `factory.md` to fill in:

- **Goal** -- a single sentence describing what the project should achieve
- **Scope / Modifiable** -- files the factory is allowed to edit
- **Scope / Read-only** -- files the factory may read but must never modify
- **Guards** -- rules the factory must never violate
- **Eval / Command** -- `python eval/score.py`
- **Eval / Threshold** -- minimum composite score to keep a change (default: `0.8`)
- **Constraints** -- additional soft rules

### Step 5: Initialize the Factory Store

```bash
uv run python -m factory init "$PROJECT_PATH"
```

This parses `factory.md` into `.factory/config.json` and creates the experiment store.

### Step 6: Run Baseline Eval

```bash
uv run python -m factory eval "$PROJECT_PATH"
```

Record the baseline score. This is the starting point -- all future changes must score at or above this level.

### Step 7: Commit

```bash
cd "$PROJECT_PATH"
git add factory.md eval/score.py .factory/
git commit -m "factory: initialize factory config and baseline eval"
```

After Review mode completes, the project is in `has_factory` state. Proceed to **Improve mode**.

---

## Mode: Improve (`has_factory`)

The factory is initialized. The factory agent acts as the **CEO/orchestrator** — it reads reports, makes decisions, and delegates ALL execution to specialist agents. It never runs evals, guards, or code analysis directly.

**Six agent roles:** **Researcher** (observe), **Strategist** (hypothesize), **Evaluator** (measure), **Builder** (implement), **Reviewer** (guard), and **Archivist** (record).

**CEO responsibilities:**
- Read reports from agents and make keep/revert decisions citing specific data
- Delegate ALL execution to specialist agents
- Handle administrative bookkeeping (begin, finalize, commit, create issues)
- NEVER run eval commands directly (delegate to Evaluator agent)
- NEVER run guard commands directly (delegate to Reviewer agent)
- NEVER analyze code directly (delegate to appropriate agent)

**Archivist as async background note-taker:**
The Archivist is NOT a one-shot step at the end. It is the CEO's persistent background writer, spawned asynchronously (fire-and-forget, non-blocking) at multiple points throughout the workflow to record decisions, findings, and patterns to the factory vault.

### Step 0: Observe (Researcher Agent)

The Researcher performs both local analysis and deep external research.

**Step 0a: Local Study**

```bash
uv run python -m factory study "$PROJECT_PATH"
```

This writes local observations to `$PROJECT_PATH/.factory/strategy/observations.md`.

**Step 0b: Deep Research (via Subagent)**

Spawn the researcher subagent to perform web-based research and vault knowledge synthesis:

```bash
claude -p "$(cat <<'PROMPT'
You are the Researcher agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/researcher.md — use Mode 2 (Research).

Project: $PROJECT_PATH

## Context
$(cat "$PROJECT_PATH/factory.md" 2>/dev/null || echo "No factory.md")
$(cat "$PROJECT_PATH/.factory/strategy/observations.md" 2>/dev/null || echo "No local observations")
$(uv run python -m factory history "$PROJECT_PATH" 2>/dev/null || echo "No experiments yet")

## Task
1. Read the local observations already generated
2. Use WebSearch to find 5-10 relevant external resources for this project
3. Use WebFetch to deeply read the top 3-5 results
4. Read the factory vault for prior knowledge: ~/factory-vault/
5. Write comprehensive research report to $PROJECT_PATH/.factory/strategy/research.md
6. Write any new external source notes to ~/factory-vault/20-Knowledge/Sources/
PROMPT
)" --dangerously-skip-permissions
```

If the deep research subagent fails, proceed to Step 1 — the Strategist can work from local observations alone.

**Async Archivist — record research findings (fire-and-forget):**

```bash
claude -p "$(cat <<'PROMPT'
You are the Archivist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/archivist.md

Project: $PROJECT_PATH

## Task (async — background note-taking)
Record the Researcher's findings to the factory vault.
1. Read: cat "$PROJECT_PATH/.factory/strategy/observations.md"
2. Read: cat "$PROJECT_PATH/.factory/strategy/research.md"
3. Write new source notes to ~/factory-vault/20-Knowledge/Sources/
4. Update the project research log in the vault
PROMPT
)" --dangerously-skip-permissions &
```

> **Note:** The `&` makes this non-blocking. The CEO does not wait for the Archivist.

### Step 1: Hypothesize (Strategist Agent)

The Strategist reads the Researcher's observations (from `.factory/strategy/observations.md`), analyzes the codebase and eval scores, and generates prioritized hypotheses.

```bash
claude -p "$(cat <<'PROMPT'
You are the Strategist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/strategist.md

Project: $PROJECT_PATH

## Context
$(uv run python -m factory history "$PROJECT_PATH" 2>/dev/null || echo "No experiments yet")

$(cat "$PROJECT_PATH/factory.md")

$(cat "$PROJECT_PATH/.factory/strategy/observations.md" 2>/dev/null || echo "No observations from Researcher")

$(cat "$PROJECT_PATH/.factory/strategy/research.md" 2>/dev/null || echo "No deep research available")

$(cat "$PROJECT_PATH/.factory/strategy/current.md" 2>/dev/null || echo "No prior strategy")

$(cd "$PROJECT_PATH" && git log --oneline -20)

$(uv run python -m factory eval "$PROJECT_PATH")

## Task
Observe the project state, analyze patterns, and write 1-3 hypotheses to
$PROJECT_PATH/.factory/strategy/current.md

Each hypothesis must be:
- Specific and scoped (one PR's worth of work)
- Tied to observations (low sub-score, missing feature, failed experiment to retry differently)
- Include expected impact on eval dimensions
PROMPT
)" --dangerously-skip-permissions
```

**Async Archivist — record strategy decisions (fire-and-forget):**

```bash
claude -p "$(cat <<'PROMPT'
You are the Archivist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/archivist.md

Project: $PROJECT_PATH

## Task (async — background note-taking)
Record the Strategist's decisions and reasoning to the factory vault.
1. Read: cat "$PROJECT_PATH/.factory/strategy/current.md"
2. Write a strategy snapshot to the vault
3. Update the project dashboard with current strategy
PROMPT
)" --dangerously-skip-permissions &
```

### Step 2: Execute (Per Hypothesis)

For each hypothesis in `strategy/current.md`, in priority order:

#### 2a. Baseline Eval (Evaluator Agent -- before)

The CEO delegates the baseline eval to the Evaluator agent. The Evaluator records the project score **before** any changes are made.

```bash
claude -p "$(cat <<'PROMPT'
You are the Evaluator agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/evaluator.md

Project: $PROJECT_PATH

## Task
Run the baseline eval and report the score.
1. Run: uv run python -m factory eval "$PROJECT_PATH"
2. Parse the JSON output
3. Print the composite score and per-dimension breakdown to stdout
4. If eval crashes, report the error clearly
PROMPT
)" --dangerously-skip-permissions
```

Save the output -- this is `score_before`. If the Evaluator reports a crash, see **Error Recovery: Eval Crash** below. Do not proceed to the Builder until a valid baseline score is recorded.

#### 2b. Begin Experiment

```bash
uv run python -m factory begin "$PROJECT_PATH" --hypothesis "<hypothesis text>"
```

This prints the experiment ID. Save it as `$EXP_ID`.

#### 2c. Create a GitHub Issue

```bash
gh issue create \
    --title "<hypothesis title>" \
    --label "implementation" \
    --body "$(cat <<'EOF'
## Context
Factory experiment $EXP_ID. Hypothesis: <hypothesis text>

## What to Build
<specific changes from the hypothesis>

## Acceptance Criteria
- [ ] <concrete outcomes>
- [ ] Tests pass
- [ ] Eval score does not regress

## Constraints
- Read CLAUDE.md before starting
- Do NOT touch files outside the declared scope in factory.md
EOF
)"
```

Save the issue number as `$ISSUE_NUM`.

#### 2d. Implement (Builder Agent)

The Builder agent implements the hypothesis as a PR. It works in isolation on a feature branch.

```bash
claude -p "$(cat <<'PROMPT'
You are the Builder agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/builder.md

## Task
Implement GitHub issue #$ISSUE_NUM in <owner>/<repo>.

## Instructions
1. Read the issue: gh issue view $ISSUE_NUM -R <owner>/<repo>
2. cd $PROJECT_PATH
3. Read CLAUDE.md and factory.md
4. git checkout -b experiment/$EXP_ID
5. Implement exactly what the issue describes
6. Run tests and evals
7. Commit and open PR targeting main

## Rules
- Implement ONLY what the issue asks for
- Do NOT modify eval/score.py or .factory/ contents
- Do NOT ask for input -- if stuck, comment on the issue and exit
- PR must target main (or delegate branch if one exists)
PROMPT
)" --dangerously-skip-permissions
```

If the Builder fails (non-zero exit, no PR opened, or builder comments a question on the issue), see **Error Recovery: Builder Failure** below. Do not proceed to the Reviewer until a PR exists.

#### 2e. Guard Check (Reviewer Agent)

The CEO delegates the guard check and code review to the Reviewer agent. The Reviewer enforces sacred rules and scope constraints on the Builder's PR branch.

```bash
BASELINE_SHA=$(git log --format=%H -1 main)
claude -p "$(cat <<'PROMPT'
You are the Reviewer agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/reviewer.md

Project: $PROJECT_PATH
Experiment: $EXP_ID
Baseline SHA: $BASELINE_SHA

## Task
Review the Builder's changes.
1. Run guard check: uv run python -m factory guard "$PROJECT_PATH" --baseline "$BASELINE_SHA" --check-scope
2. Read the PR diff: gh pr diff <pr-number> -R <owner>/<repo>
3. Assess code quality against acceptance criteria
4. Print your verdict to stdout: PASS or FAIL with details
PROMPT
)" --dangerously-skip-permissions
```

- If the Reviewer reports `PASS` --> proceed to Evaluator (Step 2f)
- If the Reviewer reports `FAIL` or any `VIOLATION:` --> **revert and finalize as error** (see Error Recovery: Guard Violation below). Do not run the post-change eval.

#### 2f. Post-change Eval (Evaluator Agent -- after)

The CEO delegates the post-change eval to the Evaluator agent. The Evaluator scores the project **after** the Builder's changes, on the PR branch.

```bash
claude -p "$(cat <<'PROMPT'
You are the Evaluator agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/evaluator.md

Project: $PROJECT_PATH

## Task
Run the post-change eval and report the score.
1. Run: uv run python -m factory eval "$PROJECT_PATH"
2. Parse the JSON output
3. Print the composite score and per-dimension breakdown to stdout
4. Compare against baseline score: $SCORE_BEFORE
5. State whether the hypothesis was validated
6. If eval crashes, report the error clearly
PROMPT
)" --dangerously-skip-permissions
```

Save the output -- this is `score_after`. If the Evaluator reports a crash, see **Error Recovery: Eval Crash** below.

#### 2g. Decide: Keep or Revert

Compare `score_after` vs `score_before`:

- **Keep** -- if `score_after >= score_before` and the eval threshold is met:
  - Merge the PR: `gh pr merge <pr-number> -R <owner>/<repo>`
  - Finalize the experiment:
    ```bash
    uv run python -m factory finalize "$PROJECT_PATH" \
        --id $EXP_ID \
        --verdict keep \
        --hypothesis "<hypothesis>" \
        --summary "<what changed>" \
        --issue $ISSUE_NUM \
        --pr $PR_NUM
    ```

- **Revert** -- if score regressed or threshold not met:
  - Close the PR without merging: `gh pr close <pr-number> -R <owner>/<repo>`
  - Revert any changes: `git checkout main`
  - Finalize the experiment:
    ```bash
    uv run python -m factory finalize "$PROJECT_PATH" \
        --id $EXP_ID \
        --verdict revert \
        --hypothesis "<hypothesis>" \
        --summary "<what changed -- reverted due to score regression>" \
        --issue $ISSUE_NUM
    ```

**Async Archivist — record experiment outcome (fire-and-forget):**

```bash
claude -p "$(cat <<'PROMPT'
You are the Archivist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/archivist.md

Project: $PROJECT_PATH

## Task (async — background note-taking)
Record the experiment outcome and decision rationale.
1. Read experiment history: uv run python -m factory history "$PROJECT_PATH"
2. Write an experiment note for experiment $EXP_ID (verdict: $VERDICT)
3. Record the decision rationale: score_before=$SCORE_BEFORE, score_after=$SCORE_AFTER
4. Update the project dashboard with the latest experiment result
PROMPT
)" --dangerously-skip-permissions &
```

> **Ad-hoc archiving:** At any point during the workflow, if the CEO observes a cross-project pattern or has something worth remembering, spawn an async Archivist to record it to the vault.

### Step 3: Finalize Archive (Archivist Agent)

The Archivist has been recording throughout the workflow via async background spawns. This final step ensures completeness and updates MEMORY.md.

```bash
uv run python -m factory archive "$PROJECT_PATH"
```

This reads the experiment history, writes structured archive files to `.factory/archive/`, and regenerates MEMORY.md. If the command fails, log the error — the async Archivist notes written earlier still provide coverage.

### Step 4: Notify

```bash
uv run python -m factory notify "$PROJECT_PATH"
```

### Step 5: Commit Factory State

```bash
cd "$PROJECT_PATH"
git add .factory/
git commit -m "factory: log experiment results and update strategy"
```

---

## Parallel Execution Protocol

For hypotheses with non-overlapping file scopes, execute them in parallel:

1. **Prepare all experiments**: For each independent hypothesis:
   - `factory begin --hypothesis "..."` --> get $EXP_ID
   - Create branch: `git branch experiment/$EXP_ID main`
   - Create GitHub issue

2. **Spawn builders in parallel**: Launch all builders simultaneously
   - Each builder works in an isolated worktree (via `isolation: worktree`)
   - Builders do not share state -- they read from the issue and write to their branch

3. **Review independently**: As each builder completes:
   - Spawn Reviewer to check guards and code quality
   - Spawn Evaluator to score the changes
   - CEO makes keep/revert decision

4. **Merge in priority order**: Merge kept experiments from highest to lowest priority
   - After each merge, re-evaluate remaining experiments for conflicts
   - If a merge causes conflicts with a pending experiment, rebase and re-eval

### Scaling Rules
- Simple improvements (1-2 hypotheses): sequential execution
- Moderate scope (3-5 hypotheses): parallel builders, sequential review
- Large scope (5+ hypotheses): wave-based execution -- batch into waves of 3-5

---

## Decision Framework (from Persona)

When making keep/revert decisions, apply these heuristics:

1. **Simple > Complex**: Prefer the simpler change. If two approaches achieve similar scores, keep the one with fewer lines changed.
2. **Multi-signal evaluation**: Never decide based on a single metric. Check: tests pass, lint clean, score improved, no guard violations, code is readable.
3. **Cost consciousness**: Track token/API costs per experiment. Prefer cheaper approaches for equivalent outcomes.
4. **Quality bar** (all must be true to keep):
   - Works correctly (tests pass)
   - Observable (changes are logged/traced)
   - Evaluated (scores measured before and after)
   - Documented (clear commit messages, PR description)
   - Maintainable (clean code, no hacks)
5. **When stuck**: Pick the simpler option, record reasoning in the vault, move on.

---

## Sacred Rules

These rules are **inviolable**. They are checked by `uv run python -m factory guard` before any change is kept. A violation means the change is reverted, no exceptions.

1. **Do not delete or overwrite existing tests** -- tests may be extended but never removed
2. **Do not modify files outside the declared scope** -- `factory.md` defines which files are modifiable
3. **Do not introduce secrets or credentials** -- no API keys, tokens, or passwords in the repo
4. **Do not lower the eval threshold** -- the bar only goes up
5. **Do not skip the eval step** -- every change must be scored before it can be kept
6. **Do not merge without guard check passing** -- `uv run python -m factory guard` must print `clean`

---

## Error Recovery

### Builder Failure

If the builder invocation fails (non-zero exit, no PR opened, or builder comments a question on the issue):

1. Read the issue comments: `gh issue view $ISSUE_NUM --comments -R <owner>/<repo>`
2. If the builder posted a question, answer it and re-invoke the builder
3. If the builder crashed, finalize the experiment as error:
   ```bash
   uv run python -m factory finalize "$PROJECT_PATH" \
       --id $EXP_ID \
       --verdict error \
       --hypothesis "<hypothesis>" \
       --notes "Builder failed: <error summary>"
   ```
4. Move to the next hypothesis -- do not retry the same failure more than once

### Eval Crash

If `uv run python -m factory eval` fails (non-zero exit without producing a valid score):

1. Check the eval script: `cat "$PROJECT_PATH/eval/score.py"`
2. Check for syntax errors or missing dependencies
3. If fixable, fix the eval script and retry
4. If not fixable, finalize the experiment as error:
   ```bash
   uv run python -m factory finalize "$PROJECT_PATH" \
       --id $EXP_ID \
       --verdict error \
       --notes "Eval crashed: <error output>"
   ```

### Guard Violation

If `uv run python -m factory guard` reports violations:

1. The change **must be reverted** -- no exceptions
2. Close the PR without merging: `gh pr close <pr-number> -R <owner>/<repo>`
3. Revert to the pre-experiment state: `git checkout main`
4. Finalize as revert with the violation details:
   ```bash
   uv run python -m factory finalize "$PROJECT_PATH" \
       --id $EXP_ID \
       --verdict revert \
       --notes "Guard violation: <violation details>"
   ```
5. Record the violation in `strategy/current.md` under Anti-patterns so it is not repeated

---

## Context Preservation

Factory sessions can be long-running. Save state proactively so work survives context compaction.

### When to Save

- After completing any mode (Build, Discover, Review, Improve)
- After each experiment is finalized
- After updating strategy
- When the conversation is getting long (many tool calls, large diffs)

### What to Save

Write `$PROJECT_PATH/.factory/strategy/current.md` with:

```markdown
## Strategy -- <date>

### Observations
- Current composite score: <score>
- Weakest eval dimension: <name> (<score>)
- Last 3 experiments: <ids, verdicts, deltas>
- Pattern: <what you notice>

### Hypotheses

#### H1: <short title>
- **What:** <specific change>
- **Why:** <reasoning>
- **Expected impact:** <which scores improve>
- **Priority:** <high/medium/low>

### Anti-patterns to Avoid
- <changes that failed before and why>

### Session State
- **Mode:** <Build/Discover/Review/Improve>
- **Current phase:** <what step we're on>
- **Active experiments:** <IDs, branches, PR numbers>
- **Next action:** <exactly what to do next>
```

### Recovery

If context has been compacted and prior details are lost:

1. Read `$PROJECT_PATH/.factory/strategy/current.md`
2. Run `uv run python -m factory history "$PROJECT_PATH"` to see experiment log
3. Check open issues and PRs: `gh issue list -R <owner>/<repo> --state open`
4. Continue from the "Next action" in the strategy file
