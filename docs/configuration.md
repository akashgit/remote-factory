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

Controls hypothesis generation constraints per cycle. The Strategist clears as many backlog items as possible and adds at most `max_new` new items:

```markdown
## Hypothesis Budget
- min_growth: 2
- max_new: 2
```

- **min_growth**: Minimum hypotheses targeting growth dimensions (guaranteed, never cannibalized)
- **max_new**: Maximum new items the Strategist may add to the backlog per cycle

Override per-run: `factory ceo ~/my-project --min-growth 3 --max-new 1`

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

### `## Research Target`

Only for research/benchmark projects. Defines the metric to improve iteratively. When present, auto-detection routes to research mode instead of improve mode.

```markdown
## Research Target
- objective: maximize SWE-bench resolve rate
- metric: resolved/total
- target: 0.35
- run_command: python run_benchmark.py
- result_path: results/output.json
- result_parser: json
- timeout: 3600
```

| Field | Purpose |
|-------|---------|
| `objective` | Human-readable description of the research goal |
| `metric` | Key to extract from results (JSON path or regex) |
| `target` | Goal value — experiments stop when this is reached |
| `run_command` | Shell command to execute the benchmark/evaluation |
| `result_path` | Where the run command writes results |
| `result_parser` | How to parse results: `json`, `regex`, or `exit_code` |
| `timeout` | Maximum seconds for the run command |

### `## Mutable Surfaces`

Files the Builder is allowed to modify during research experiments. One glob pattern per line. Only used in research mode.

```markdown
## Mutable Surfaces
- src/**/*.py
- config/*.yaml
```

### `## Fixed Surfaces`

Ground truth files, test data, and eval infrastructure. These are fingerprinted for leakage detection and must never be modified. One glob pattern per line. Only used in research mode.

```markdown
## Fixed Surfaces
- tests/gold/*.json
- eval/**/*.py
- data/benchmark/*.jsonl
```

### `## Research Constraints`

Additional rules for the research loop. Only used in research mode.

```markdown
## Research Constraints
- Do not use GPT-4 (cost constraint)
- Each experiment must complete within 30 minutes
```

### `## Cost Budget`

Per-cycle or total budget constraints for research experiments.

```markdown
## Cost Budget
$5/cycle, $50 total
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

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `FACTORY_VAULT_PATH` | Obsidian vault for persistent cross-project memory | *(none — vault features disabled)* | Optional |
| `FACTORY_PROJECTS_DIR` | Parent directory for prompt-created projects | `~/factory-projects` | Optional |
| `FACTORY_PLAYBOOKS_DIR` | Directory for ACE-evolved agent playbooks | `~/.factory/playbooks` | Optional |
| `FACTORY_MODEL` | Model override for agent subprocesses | *(Claude Code default)* | Optional |
