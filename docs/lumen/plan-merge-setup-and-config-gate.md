# Plan: Merge Preflight+Study and Add Config Approval Gate

## Goal

Simplify the Lumen workflow's opening nodes and add a user-facing config approval gate before expensive RL training begins.

## Current State

The workflow has 5 nodes:

```
preflight → study → lumen_context_agent → rl_train → check_gate ──(reloop)──→ lumen_context_agent
```

- `preflight` (FnNode): checks conda env, detects GPUs, creates run directory, writes resolved config
- `study` (FnNode): runs `add_sota_to_instruction.py` to update SOTA info in the task's instruction.md

Both are pre-training setup. No reason to be separate nodes.

## Changes

### 1. Merge `preflight` + `study` → single `setup` FnNode

**File:** `factory/workflow/contributed/lumen/workflow.py`

- Delete the `study` node
- Rename `preflight` → `setup`
- Append the study command to the setup node's command string (joined with `&&`)
- The merged command:
  ```bash
  cd {project_path} && \
  python3 -m factory.lumen.preflight --project-path {project_path} && \
  TASK=$(python3 -c "import json; print(json.load(open('<_CFG>'))['task_name'])") && \
  python3 benchmarks/einsteinarena/tools/add_sota_to_instruction.py $TASK || true
  ```
- Update `writes` to include both preflight outputs
- Update `reads` on the merged node (study used to read `_CFG`, which setup itself writes — no external reads needed)
- Remove the `preflight → study` edge
- Update `study → lumen_context_agent` edge to `setup → config_gate`
- Update `start_node` from `"preflight"` to `"setup"`

### 2. Add `config_gate` GateNode after `setup`

**File:** `factory/workflow/contributed/lumen/workflow.py`

Add a new GateNode between `setup` and `lumen_context_agent`:

```python
nodes["config_gate"] = GateNode(
    id="config_gate",
    evaluator_type="user",
    evaluator_command=<print resolved config summary>,
    reads={_CFG},
)
```

The `evaluator_type="user"` already has executor support (`factory/workflow/executor.py:849-852`):
- **Headless** (`auto_approve=True`): auto-proceeds, logs `gate.auto_approved`
- **Interactive**: currently also proceeds (same as headless) — the gate is a placeholder for future interactive prompting by the CEO agent

To make the gate useful even in headless mode, add an `evaluator_command` that prints the resolved config as a readable summary. The executor runs the fn evaluator first when `evaluator_command` is set, so the config gets printed to stdout regardless of mode.

**Wait — `evaluator_type="user"` skips `evaluator_command` entirely.** The executor checks `evaluator_type` first and returns immediately for `"user"`. So to print the config summary, we need a different approach:

**Option chosen:** Use `evaluator_type="fn"` with a command that:
1. Prints the resolved config as a human-readable summary
2. Always outputs `pass` at the end

Then, when running interactively via the CEO agent (not the headless executor), the CEO's SKILL.md will have a "Steering Point" directive telling it to present the config to the user and wait for approval — exactly like design mode's strategy approval gate. The fn gate ensures the config is printed even in headless mode.

```python
nodes["config_gate"] = GateNode(
    id="config_gate",
    evaluator_type="fn",
    evaluator_command=(
        "cd {project_path} && python3 -c \""
        "import json;"
        f"cfg = json.load(open('{_CFG}'));"
        "print('=== Run Config ===');"
        "for k in ['task_name','model_path','num_gpus','rollout_tp','lora_rank',"
        "'learning_rate','kl_coef','temperature','num_rollouts_per_prompt','mock']:"
        "  print(f'  {k}: {cfg.get(k, \"—\")}');"
        "print();"
        "print('pass: config ready');"
        "\""
    ),
    reads={_CFG},
)
```

New edges:
```
setup → config_gate → lumen_context_agent
```

### 3. Update edges

Old:
```python
Edge(source="preflight", target="study"),
Edge(source="study", target="lumen_context_agent"),
```

New:
```python
Edge(source="setup", target="config_gate"),
Edge(source="config_gate", target="lumen_context_agent"),
```

### 4. Update tests

**File:** `tests/test_lumen_preflight.py`

No changes needed — preflight tests test the Python module, not the workflow node.

**File:** `tests/test_workflow_cli.py`

Run existing tests to verify no regressions. The workflow validation tests will automatically pick up the new graph structure.

## Resulting Workflow

```
setup → config_gate → lumen_context_agent → rl_train → check_gate ──(reloop)──→ lumen_context_agent
```

4 nodes instead of 5. Config is displayed before training starts.
