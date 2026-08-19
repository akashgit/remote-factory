---
name: workflow-lumen
description: "Run the lumen workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Lumen Workflow

The user wants: **$ARGUMENTS**

## Step: Setup

```bash
cd $PROJECT_PATH && LUMEN_PYTHON=/workspace/home/asherding/code/remote-factory/factory/lumen/.venv/bin/python python3 /workspace/home/asherding/code/remote-factory/factory/lumen/preflight.py --project-path $PROJECT_PATH && TASK=$(/workspace/home/asherding/code/remote-factory/factory/lumen/.venv/bin/python -c "import json; print(json.load(open('.factory/lumen/.running/config.json'))['task_name'])") && /workspace/home/asherding/code/remote-factory/factory/lumen/.venv/bin/python tools/add_sota_to_instruction.py $TASK || true
```

### Steering Point — Config Gate (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input

## Phase 1: Lumen Context Agent

```bash
factory agent lumen_context_agent --task "You are the Lumen Context Agent. Generate 8 optimization prompts.

Read the run config at .factory/lumen/.running/config.json to find task_name and iteration.
Read: <task_name>/instruction.md
Read: .factory/lumen/.running/state.json for current iteration.

If iteration > 0, read the previous iteration's evaluation_results.json from .factory/lumen/.running/iteration_<prev>/evaluation_results.json

Output: .factory/lumen/.running/iteration_<current>/prompts.json

Follow the format specified in factory/agents/prompts/lumen_context_agent.md
Read: .factory/lumen/.running/config.json, .factory/lumen/.running/state.json
Write output to: .factory/lumen/.running/iteration_*/prompts.json" --project "$PROJECT_PATH" --timeout 1800
```

```bash
# Artifact verification: lumen_context_agent
_vfail=0
_f="$PROJECT_PATH/.factory/lumen/.running/iteration_*/prompts.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: lumen_context_agent: .factory/lumen/.running/iteration_*/prompts.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: lumen_context_agent: .factory/lumen/.running/iteration_*/prompts.json is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=lumen_context_agent" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: lumen_context_agent artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=lumen_context_agent" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Step: Rl Train

```bash
cd $PROJECT_PATH && PYTHONPATH=/workspace/home/asherding/code/remote-factory:$PYTHONPATH /workspace/home/asherding/code/remote-factory/factory/lumen/.venv/bin/python /workspace/home/asherding/code/remote-factory/factory/lumen/train.py --config .factory/lumen/.running/config.json
```

## Step: Eval Stats

```bash
cd $PROJECT_PATH && python3 /workspace/home/asherding/code/remote-factory/factory/lumen/eval_stats.py
```

### Gate — Check Gate (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && python3 /workspace/home/asherding/code/remote-factory/factory/lumen/check_gate.py
```

*On RELOOP: return to `lumen_context_agent` (max 3 iterations)*

## Step: Finalize

```bash
cd $PROJECT_PATH && python3 /workspace/home/asherding/code/remote-factory/factory/lumen/finalize.py
```
