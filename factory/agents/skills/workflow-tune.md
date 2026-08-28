# /workflow-tune — Iterative Workflow Tuning

Observe a CEO run, identify workflow issues from the transcript, and fix them via `--overwrite`.

## When to Use

- After a CEO run produces suboptimal results (missed tests, skipped steps, wrong agent order)
- When you want to systematically improve a workflow mode's pipeline
- When the user asks to tune or optimize a workflow

## Procedure

### Step 1: Dispatch Baseline Run

```bash
factory tmux <project_path> --mode <mode>
```

Wait for the session to complete. Monitor progress:

```bash
factory tmux-capture <project_path> --lines -200
```

### Step 2: Analyze Transcript

Once the session completes, capture the full output:

```bash
factory tmux-capture <project_path> --lines -500
```

Read the results:

```bash
cat <project_path>/.factory/reviews/health-check.md
cat <project_path>/.factory/reviews/adversarial-qa.md
factory history <project_path>
```

Identify what went wrong or could be improved. Common patterns:
- Builder didn't run tests -> overwrite to add test instructions
- QA was skipped -> overwrite to enforce QA step
- Wrong agent order -> overwrite to reorder edges
- Missing verification -> overwrite to add a gate node

### Step 3: Formulate Overwrite

Write a natural-language directive describing the fix:

```bash
factory tmux <project_path> --mode <mode> --overwrite 'The builder must run pytest after implementing. Add test verification to the builder prompt.'
```

### Step 4: Compare Results

After the overwrite run completes:

```bash
factory eval <project_path>
factory history <project_path>
```

Compare the baseline and overwrite runs:
- Did the identified issue get fixed?
- Did eval scores improve or regress?
- Were there any new failures?

### Step 5: Iterate or Stop

- If the overwrite improved results, record the successful overwrite text
- If it regressed, try a different overwrite formulation
- Stop when the workflow produces satisfactory results

## Tips

- Start with small, focused overwrites (one change at a time)
- The overwrite is interpreted by a strategist agent into structured mutations
- Valid mutations: update_node (change fields), remove_node, add_edge, remove_edge, mutate_knob (change OptKnob values within bounds)
- The overwrite only affects the current session — it does not persist
