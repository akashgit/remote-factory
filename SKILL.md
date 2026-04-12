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

The factory is initialized. Run the inner improvement loop: observe the current state, form hypotheses, execute changes via builder agents, guard-check, eval, and keep or revert.

### Step 1: Observe (Strategist Agent)

Invoke the Strategist agent to gather context and generate hypotheses:

```bash
claude -p "$(cat <<'PROMPT'
You are the Strategist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/strategist.md

Project: $PROJECT_PATH

## Context
$(uv run python -m factory history "$PROJECT_PATH" 2>/dev/null || echo "No experiments yet")

$(cat "$PROJECT_PATH/factory.md")

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

### Step 2: Execute (Per Hypothesis)

For each hypothesis in `strategy/current.md`, in priority order:

#### 2a. Record Baseline Score

```bash
uv run python -m factory eval "$PROJECT_PATH"
```

Save the output -- this is `score_before`.

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

#### 2d. Build (Builder Agent)

Invoke the Builder agent to implement the issue:

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

#### 2e. Guard Check (Reviewer Agent)

After the builder finishes, check sacred rules:

```bash
BASELINE_SHA=$(git log --format=%H -1 main)
uv run python -m factory guard "$PROJECT_PATH" --baseline "$BASELINE_SHA"
```

- If output is `clean` --> proceed to eval
- If output shows `VIOLATION:` --> **revert and finalize as error** (see Error Recovery)

#### 2f. Eval After

```bash
uv run python -m factory eval "$PROJECT_PATH"
```

Save the output -- this is `score_after`.

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

### Step 3: Log (Archivist Agent)

After all hypotheses have been executed, invoke the Archivist to record the cycle:

```bash
claude -p "$(cat <<'PROMPT'
You are the Archivist agent for the Software Factory.
Load your base prompt from ~/factory-projects/remote-factory/factory/agents/prompts/archivist.md

Project: $PROJECT_PATH

## Task
1. Read the experiment history: uv run python -m factory history "$PROJECT_PATH"
2. Write Obsidian experiment notes for each new experiment
3. Update the project dashboard
4. Write a strategy snapshot

Target vault: the user's personal Obsidian vault pathWork/Factory/
PROMPT
)" --dangerously-skip-permissions
```

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
