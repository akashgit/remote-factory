---
name: workflow-skill-refine
description: "Verified skill generation pipeline — templatize, review, guard, split. Converts Pydantic workflow graphs into verified SKILL.md files with annotations. Use to regenerate skills after workflow definition changes."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Skill Refine Workflow

The user wants: **$ARGUMENTS**

## Step: Dag Sort

```bash
factory workflow show $PROJECT_PATH
```

## Step: Templatize

```bash
factory workflow export-skills --templatize $PROJECT_PATH
```

## Phase 1: Skill Reviewer — Review Agent

```bash
factory agent skill_reviewer --task "Review and refine the templatized skill document. You may ONLY modify values inside {{slot_name::value markers. Do NOT change any text outside markers, annotations, or structure. Use the provided context bundle (agent prompts, CLI docs, edge topology) to make informed improvements to timeouts, task prompts, gate prompts, failure actions, and finalize commands.
Read: .factory/strategy/templatized-skill.md
Write output to: .factory/strategy/refined-skill.md}}" --project "$PROJECT_PATH" --timeout 600
```

### Gate — Guard (Automated)

```bash
python3 -c "from factory.workflow.guard import check; from pathlib import Path; s = Path('$PROJECT_PATH/.factory/strategy/templatized-skill.md').read_text(); r = Path('$PROJECT_PATH/.factory/strategy/refined-skill.md').read_text(); result = check(s, r); print(result.verdict)"
```

- **PROCEED** → continue to `split`

*On RELOOP: return to `review_agent` (max 3 iterations)*

## Step: Split

```bash
factory workflow export-skills --split $PROJECT_PATH
```
