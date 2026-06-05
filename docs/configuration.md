# Configuration Reference

Each re:factory-managed project uses a `factory.md` file at its root. The CEO auto-generates this during discovery mode, but you can edit it manually.

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

Glob patterns defining which files re:factory may edit. Anything outside scope triggers a guard violation.

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

### `## Clean PR`

Strips non-essential artifacts (eval scripts, benchmarks, `.factory/` data, eval test files) from PRs before pushing to external repositories. Useful when contributing factory-managed code to upstream repos that don't want factory infrastructure.

```markdown
## Clean PR
- clean_pr: true
- clean_pr_include: ["src/**", "lib/**"]
- clean_pr_exclude: ["src/internal/**"]
```

| Field | Purpose | Default |
|-------|---------|---------|
| `clean_pr` | Enable clean PR mode | `false` |
| `clean_pr_include` | Include-only glob patterns — if set, only matching files survive | `[]` |
| `clean_pr_exclude` | Additional exclude patterns beyond defaults | `[]` |

Default excludes (always applied): `eval/score.py`, `benchmarks/**`, `tests/eval_*`, `.factory/**`. A file matched by both include and exclude is excluded (exclude wins).

Resolution precedence: CLI flag (`--clean-pr` / `--no-clean-pr`) > `config.json` > default (`false`).

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

### `## Inner Loop`

Multi-run configuration for research mode. Runs the evaluation harness multiple times per cycle and aggregates the metric. Useful for stochastic pipelines where a single run doesn't give a reliable signal. Only used in research mode.

```markdown
## Inner Loop
- runs_per_cycle: 5
- aggregate: mean
- plateau_threshold: 3
- max_inner_runs_per_cycle: 10
```

| Field | Purpose | Default |
|-------|---------|---------|
| `runs_per_cycle` | Number of times to run the harness per cycle | `1` |
| `aggregate` | How to combine scores: `mean`, `median`, `max`, `all_pass` | `mean` |
| `plateau_threshold` | Consecutive non-improving cycles before triggering outer loop | `3` |
| `max_inner_runs_per_cycle` | Optional cap on runs per cycle | None |

### `## Outer Loop Surfaces`

Surface scoping for inner/outer loop transitions. When inner loop improvements plateau, re:factory expands the Builder's scope to include outer surfaces for architectural changes. Only used in research mode.

```markdown
## Outer Loop Surfaces
- max_outer_cycles: 5
- inner: prompts/*.md
- inner: config/*.yaml
- outer: src/**/*.py
- outer: agents/**/*.md
```

| Field | Purpose |
|-------|---------|
| `max_outer_cycles` | Maximum outer loop expansions before stopping |
| `inner: <glob>` | Narrow surfaces used during inner loop (one per line) |
| `outer: <glob>` | Additional surfaces unlocked after plateau (one per line) |

Entries use prefix format — `inner:` and `outer:` followed by a glob pattern. Multiple entries per type are allowed.

### `## Cost Budget`

Per-cycle or total budget constraints for research experiments.

```markdown
## Cost Budget
$5/cycle, $50 total
```

## `.factory/` Directory

Generated at runtime by re:factory. Add to `.gitignore` — do not edit manually:

```
.factory/
├── config.json              # Parsed from factory.md
├── eval_profile.json        # Discovered eval dimensions
├── results.tsv              # Append-only experiment history
├── events.jsonl             # Structured event log
├── performance_report.json  # Aggregated verdicts, observations, stats
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
│   ├── backlog.md
│   └── insights.md
├── reviews/
│   ├── <role>-latest.md
│   └── ceo-verdict-<role>.md
├── archive/                 # Archivist notes
│   ├── experiments/
│   ├── strategies/
│   ├── sources/
│   └── patterns/
└── agents/                  # Per-project prompt overrides
```

## User Configuration (`~/.factory/config.toml`)

All `FACTORY_*` environment variables can also be set in `~/.factory/config.toml`. Env vars remain fully supported (for CI, Docker, scripting) — config.toml is additive.

### Precedence (highest wins)

```
CLI flag  >  env var  >  profile credential  >  config.toml [defaults]  >  hardcoded default
```

### Example config.toml

```toml
[defaults]
runner = "claude"                        # CLI backend: "claude" or "bob"
model = ""                               # Claude model for agent subprocesses
projects_dir = "~/factory-projects"      # Root for factory-managed projects

[credentials.vertex]
CLAUDE_CODE_USE_VERTEX = "1"
ANTHROPIC_VERTEX_PROJECT_ID = "my-gcp-project"
CLOUD_ML_REGION = "us-east5"

[credentials.bob]
FACTORY_RUNNER = "bob"
BOBSHELL_API_KEY = "..."
```

### Commands

```bash
factory config edit                      # Open config in $EDITOR (creates template if missing)
factory config show                      # Show resolved config (secrets masked)
factory config show --reveal             # Show full values including secrets
factory config migrate                   # Create starter config from current env vars
```

### Credential profiles

Profiles let you switch between environments without juggling env vars:

```bash
factory ceo ~/my-project --profile vertex
factory run ~/my-project --profile bob --loop
factory agent researcher --task "..." --project ~/my-project --profile vertex
```

Profile credentials are injected via `os.environ.setdefault()`, so pre-existing env vars always win.

### Security

- Config file is created with `0o600` permissions (owner read/write only)
- `factory config show` masks secrets by default (keys containing "key", "token", "secret", "password")
- Profile names are validated: `[a-zA-Z0-9_-]+` only (no path traversal)
- Credential keys must be valid shell identifiers: `[A-Z_][A-Z0-9_]*`

### Available defaults keys

| Key | Env var equivalent | Default |
|-----|-------------------|---------|
| `runner` | `FACTORY_RUNNER` | `claude` |
| `model` | `FACTORY_MODEL` | *(Claude Code default)* |
| `projects_dir` | `FACTORY_PROJECTS_DIR` | `~/factory-projects` |
| `vault_path` | `FACTORY_VAULT_PATH` | *(unset)* |
| `playbooks_dir` | `FACTORY_PLAYBOOKS_DIR` | `~/.factory/playbooks` |
| `registry_dir` | `FACTORY_REGISTRY_DIR` | `~/.factory` |
| `managed_dirs` | `FACTORY_MANAGED_DIRS` | *(unset)* |
| `runner_quiet` | `FACTORY_RUNNER_QUIET` | *(unset)* |
| `bob_dry_run` | `FACTORY_BOB_DRY_RUN` | *(unset)* |
| `bob_max_invocations_per_cycle` | `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` | `8` |
| `ceo_respawn_disabled` | `FACTORY_CEO_RESPAWN_DISABLED` | *(unset)* |
| `ceo_max_respawns` | `FACTORY_CEO_MAX_RESPAWNS` | `3` |

## Environment Variables

All environment variables listed below can alternatively be set in `~/.factory/config.toml` (see above). Env vars are still supported for CI, Docker, and scripting.

| Variable | Purpose | Default |
|----------|---------|---------|
| `FACTORY_PROJECTS_DIR` | Parent directory for projects created from prompts | `~/factory-projects` |
| `FACTORY_MODEL` | Model override for agent subprocesses | *(Claude Code default)* |
| `FACTORY_PLAYBOOKS_DIR` | Directory for ACE-evolved agent playbooks | `~/.factory/playbooks` |
| `FACTORY_REGISTRY_DIR` | Override global registry location | `~/.factory` |
| `FACTORY_RUNNER` | CLI backend: `claude` or `bob` | `claude` |

See [Setup Guide — Environment Variables](setup.md#environment-variables) for the full list, including Claude Code authentication, Bob Shell, notifications, and advanced CEO options.
