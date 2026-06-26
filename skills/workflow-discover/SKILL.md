---
name: workflow-discover
description: "Discover mode — auto-discover eval dimensions and generate the eval harness. Use when the project state is no_factory (repo exists but no factory setup). Runs factory discover, verifies the eval profile, and re-detects state."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Discover Workflow

The user wants: **$ARGUMENTS**

## Step: Discover


```bash
factory discover $PROJECT_PATH
```

### CEO Review — Discover

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/eval_profile.json`, `eval/score.py`
3. Assess: Verify the discovered eval profile makes sense. Read .factory/eval_profile.json and eval/score.py. Check: Are the dimensions relevant to this project? Does score.py look correct? Any missing dimensions?
4. Write verdict to `.factory/reviews/ceo-verdict-discover.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `discover` (max 3 iterations)*

## Step: Redetect


```bash
factory detect $PROJECT_PATH
```

## Phase: Archivist

Fire-and-forget: archive the discovered eval dimensions.

```bash
factory agent archivist --task "Archive the discovered eval dimensions and generated eval harness. Read .factory/eval_profile.json and eval/score.py. Record what dimensions were discovered and why.
Write output to: .factory/archive/discover.md" --project "$PROJECT_PATH" --model haiku --timeout 300 &
```
