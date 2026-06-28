# Coding Playbook — Factory Development Guide

## Workflow-to-Skill Compilation Pipeline

All factory modes are defined as directed graphs (Pydantic models) that compile into two execution formats. The pipeline has three layers:

```
factory/workflow/definitions.py       ← Source of truth (Pydantic graph models)
       │
       ├──► factory/workflow/executor.py    (headless: walks the DAG)
       │       factory workflow run <name> --project /path
       │
       └──► factory/workflow/skill_export.py  (interactive: graph → SKILL.md)
               WORKFLOW_META dict + compiler
               └── skills/workflow-*/SKILL.md  (generated output)
```

**CARDINAL RULE: Never edit `skills/workflow-*/SKILL.md` files directly.** They are generated artifacts. All changes must go through:

1. Edit the workflow definition in `factory/workflow/definitions.py`, or
2. Edit the metadata in `WORKFLOW_META` in `factory/workflow/skill_export.py`
3. Run `factory workflow export-skills` to regenerate all SKILL.md files

Editing SKILL.md directly causes drift between graph definitions and skills, leading to Sacred Rule violations (see experiment #5, issue #812).

## Adding or Modifying Workflow Nodes

Each workflow function in `definitions.py` returns a `Workflow` Pydantic model containing typed nodes connected by `Edge` objects. Six node types are available:

### AgentNode — spawn a specialist agent

```python
nodes["researcher"] = AgentNode(
    id="researcher",
    role=AgentRole.RESEARCHER,
    prompt_template="Research the problem space. Write findings to .factory/strategy/research.md",
    writes={".factory/strategy/research.md"},
)
```

### FnNode — run a shell command

```python
nodes["begin"] = FnNode(
    id="begin",
    command='factory begin {project_path} --hypothesis "$HYPOTHESIS"',
    writes={".factory/experiments/current_id"},
)
```

### GateNode — CEO decision point (PROCEED / RELOOP / HALT)

```python
nodes["gate_research"] = GateNode(
    id="gate_research",
    evaluator_type="agent",
    evaluator_role=AgentRole.CEO,
    gate_prompt="Is the research adequate? Check for coverage gaps.",
    reads={".factory/strategy/research.md"},
)
```

Connect nodes with edges. Conditional edges fire on specific gate verdicts:

```python
Edge(source="gate_qa", target="gate_precheck", condition=VerdictType.PROCEED)
Edge(source="gate_qa", target="builder",        condition=VerdictType.RELOOP)
```

See `factory/workflow/README.md` for the full graph engine documentation, including `ForkNode`, `JoinNode`, `Study`, and the validation system.

## Verifying Changes

After modifying workflow definitions:

```bash
factory workflow validate <name>    # Validate graph structure (reads/writes, edges, cycles)
factory workflow show <name>        # Display graph visualization
factory workflow export-skills      # Regenerate SKILL.md files
git diff skills/                    # Review what changed in generated output
```

## CLI Changes

When adding or modifying CLI flags for a mode (e.g. `--focus` for `--mode create`), edit `factory/cli.py` directly — CLI argument handling is not generated from workflow definitions. The two systems connect at `_build_ceo_task()`, which assembles the CEO prompt from CLI args and passes it to the runner.
