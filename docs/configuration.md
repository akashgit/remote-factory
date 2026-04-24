# Configuration Reference

Each Factory-managed project uses a `factory.md` file at its root. The CEO auto-generates this during discovery mode, but you can edit it manually.

## Minimal Configuration

```markdown
## Goal
One sentence describing what the project should achieve.

## Scope
### Modifiable
- src/**
- tests/**

## Guards
- Do not delete existing tests
- Do not modify files outside scope

## Eval
### Command
pytest --tb=short -q

### Threshold
0.8
```

## All Sections

### `## Goal` (required)

What the project should achieve. One sentence that guides the Strategist's hypotheses.

### `## Scope / Modifiable` (required)

Glob patterns defining which files the factory may edit. Anything outside scope triggers a guard violation.

```markdown
## Scope
### Modifiable
- src/**
- tests/**
- docs/**
```

### `## Guards` (required)

Inviolable rules checked before every merge. Guard violations force a revert regardless of eval score.

```markdown
## Guards
- Do not delete existing tests
- Do not modify files outside scope
- Do not remove error handling
```

### `## Eval / Command` (required)

Shell command for running project evaluation. Must produce parseable output.

### `## Eval / Threshold`

Minimum composite score to keep a change. Default: `0.8`.

### `## Target Branch`

Branch for experiment PRs. Default: `main`.

Set to a different branch (e.g. `factory/dev`) to stage all factory work separately:

```markdown
## Target Branch
factory/dev
```

Override per-run: `factory ceo ~/my-project --branch staging`

### `## Hypothesis Budget`

Controls how many hypotheses the Strategist generates per cycle:

```markdown
## Hypothesis Budget
- min_growth: 2
- min_fix: 0
- max_total: 7
```

- **min_fix**: Reserved for bugfixes (scales with open GitHub issues)
- **min_growth**: Reserved for growth dimensions (guaranteed, never cannibalized)
- **max_total**: Upper bound on total hypotheses

Override per-run: `factory ceo ~/my-project --min-growth 3 --max-total 10`

### `## Project Eval`

User-defined eval dimensions for domain-specific metrics:

```markdown
## Project Eval
- name: benchmark_accuracy
  command: python eval/benchmark.py
  parse: json
  weight: 0.6
  timeout: 300
  description: Run benchmark and report accuracy
- name: response_latency
  command: python eval/latency_test.py
  parse: exit_code
  weight: 0.4
```

See [Eval System](eval.md) for details on parse formats and scoring.

### `## Eval Weights`

Custom weight distribution across the three eval tiers:

```markdown
## Eval Weights
- hygiene: 0.25
- growth: 0.25
- project: 0.50
```

Default when project eval is present: `0.30 / 0.20 / 0.50`. Without project eval: `0.50 / 0.50`.

### `## Smoke Test`

An e2e verification command that must pass before any change is kept:

```markdown
## Smoke Test
```bash
curl -sf http://localhost:8000/health
```
```

Good smoke tests are fast (under 30s), test the core user flow, and catch integration issues that unit tests miss.

### `## Constraints`

Soft rules that guide behavior but don't block merges:

```markdown
## Constraints
- Prefer small, focused changes over large refactors
- Add tests for any new public function
```

## `.factory/` Directory

Generated at runtime by the factory. Add to `.gitignore` — do not edit manually:

```
.factory/
├── config.json           # Parsed from factory.md
├── eval_profile.json     # Discovered eval dimensions
├── results.tsv           # Append-only experiment history
├── events.jsonl          # Structured event log
├── experiments/
│   └── 001/
│       ├── hypothesis.md
│       ├── eval_before.json
│       ├── eval_after.json
│       ├── changes.diff
│       └── verdict.json
├── strategy/
│   ├── current.md
│   ├── observations.md
│   └── insights.md
├── reviews/
│   ├── <role>-latest.md
│   └── ceo-verdict-<role>.md
└── agents/               # Per-project prompt overrides
```

## Environment Variables

The Factory spawns Claude Code as subprocesses — it does not call the Claude API directly. Configure Claude Code authentication however you normally would (API key, Vertex AI, etc.).

| Variable | Purpose | Required |
|----------|---------|----------|
| `FACTORY_VAULT_PATH` | Custom Obsidian vault path | Optional |
| `FACTORY_PROJECTS_DIR` | Override default projects directory | Optional |
