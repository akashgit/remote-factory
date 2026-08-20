# Outer Loop — Evolutionary Workflow Search

The outer loop evolves workflow DAG topologies against benchmarks using evolutionary search. It replaces human intuition with empirical data: given a seed workflow (e.g. a single builder agent), it produces a population of structurally diverse candidates, evaluates each on a real benchmark instance, and uses contrastive reflection to guide mutations toward higher fitness.

## Quick Start

```bash
# 1. Set up a benchmark instance (e.g. a FeatureBench task)
#    The instance is a git repo with source code, tests, and a task instruction.

# 2. Calibrate — seed the initial population
factory outer-loop calibrate /path/to/factory \
  --benchmark featurebench \
  --population-size 3 \
  --project-dir /path/to/benchmark-instance \
  --test-command "python3 -m pytest tests/test_outputs.py -v"

# 3. Run the full evolutionary loop (in tmux for persistence)
factory ceo /path/to/factory --mode outer-loop --headless --no-worktree

# Or step-by-step:
factory outer-loop evaluate /path/to/factory --generation 0
factory outer-loop reflect /path/to/factory --generation 0
factory outer-loop evolve /path/to/factory --generation 0
factory outer-loop status /path/to/factory --check-converge
```

## Architecture

### Two-CEO Model

The outer loop uses a two-tier CEO structure:

```
OUTER LOOP CEO                              INNER LOOP (sub-CEO, one per candidate)
──────────────                              ─────────────────────────────────────
Invoked by:                                 Invoked by:
  factory ceo --mode outer-loop               InnerLoop.step() → factory ceo --mode evolve-gen0-{id}

Runs:                                       Runs:
  The evolutionary search loop                The candidate workflow on one benchmark instance

Workflow:                                   Workflow (varies per candidate):
  seed → evaluate → reflect                   e.g. builder only
  → evolve → gate → RELOOP                    e.g. builder → refiner
                                              e.g. study → builder → gate → RELOOP

Lifetime: hours (full evolution)            Lifetime: minutes (one evaluation)
```

### Pipeline

```
calibrate ──▶ evaluate ──▶ reflect ──▶ evolve ──▶ gate_converge ─┐
                  ▲                                               │
                  └──────────── RELOOP ───────────────────────────┘
                                                       │
                                               PROCEED │
                                                       ▼
                                                   promote
```

1. **Calibrate** — Seeds the initial population from a base workflow. Creates N candidates: the unmodified seed + (N-1) random mutations.
2. **Evaluate** — Runs each candidate on the benchmark instance via InnerLoop.step(). Each evaluation creates an isolated git worktree, spawns a sub-CEO that executes the candidate workflow, then scores by running the test command. Score = pytest pass rate (0.0–1.0) minus parsimony penalty.
3. **Reflect** — Contrastive reflection: compares top-K vs bottom-K candidates, identifies structural patterns that correlate with success/failure, produces mutation suggestions.
4. **Evolve** — Tournament selection + mutation. 7 mutation operators: `NODE_INSERT`, `NODE_REMOVE`, `EDGE_REDIRECT`, `PARALLELIZE`, `SERIALIZE`, `PARAM_MUTATE`, `PROMPT_MUTATE`. Reflection suggestions guide operator selection (70% guided, 30% random).
5. **Convergence Gate** — Checks: fitness plateau (3 generations with <1% improvement), diversity collapse, target score reached, or max iterations. RELOOP if not converged, PROCEED to promote if done.
6. **Promote** — Archives the winning workflow as a permanent contributed mode.

### Scoring

The score for each candidate is:

```
score = pytest_pass_rate - parsimony_penalty
```

Where:
- `pytest_pass_rate` = tests_passed / tests_total (from running the benchmark's test command)
- `parsimony_penalty` = 0.01 × number_of_nodes (simpler workflows score higher)

The cycle_summary.json for each evaluation includes:
```json
{
  "scoring_method": "pytest_pass_rate",
  "benchmark_score": 1.0,
  "test_details": {
    "tests_passed": 6.0,
    "tests_total": 6.0,
    "pass_rate": 1.0
  },
  "parsimony_penalty": 0.01,
  "score": 0.99
}
```

The `test_command` is benchmark-agnostic. The `--test-format` flag controls how output is parsed. Set both during calibration, or let them auto-resolve from a benchmark TOML config.

### Isolation

Each candidate evaluation runs in an isolated git worktree of the benchmark instance:

```
/tmp/benchmark-instance/           ← original (never modified)
/tmp/.eval-worktrees/
  wt-evolve-gen0--a1b2c3d4/       ← worktree for candidate 1
  wt-evolve-gen0--e5f6g7h8/       ← worktree for candidate 2
```

Worktrees are created before evaluation and cleaned up after scoring. This ensures candidates don't contaminate each other.

### Ephemeral Modes

Each candidate workflow is registered as a temporary mode:

```
evolve-gen0-a1b2c3d4    ← seed (1 node: builder)
evolve-gen0-e5f6g7h8    ← mutation (2 nodes: builder → refiner)
evolve-gen1-gen1_0      ← gen1 offspring (2 nodes: builder → researcher)
```

Modes are registered via `EphemeralModeRegistry` which:
- Writes workflow JSON to `.factory/outer_loop/modes/`
- Writes Python wrappers to `.factory/workflows/` (for WorkflowRegistry discovery)
- Mirrors wrappers to the target project directory (for sub-CEO resolution)
- Cleans up non-surviving modes after selection

## Modules

| Module | Purpose |
|--------|---------|
| `engine.py` | `SwarmEngine` — orchestrates the evolutionary loop |
| `evaluator.py` | `SwarmEvaluator` — fitness evaluation with caching and worktree isolation |
| `mutations.py` | 7 mutation operators + `WeightedRandomStrategy` |
| `population.py` | `Population` + `MAPElitesArchive` (4D quality-diversity grid) |
| `similarity.py` | `structural_hash`, `graph_edit_distance`, `NoveltyFilter` |
| `reflector.py` | `OuterLoopReflector` — contrastive analysis of winners vs losers |
| `mode_registry.py` | `EphemeralModeRegistry` — lifecycle management for candidate modes |
| `designer.py` | `DesignerAgent` — from-scratch workflow design |
| `models.py` | `SwarmConfig`, `Individual`, `EvalResult`, `OuterLoopState` |
| `evaluators/` | Pluggable test output parsers: `pytest`, `exit_code`, `json`, `exact_match` |
| `benchmark_config.py` | TOML-based benchmark config registry |
| `instance_prep.py` | Instance preparation and validation |
| `featurebench_evaluator.py` | pytest output parser for partial credit scoring (backward compat) |
| `featurebench_inner_loop.py` | Bridges outer loop evaluation to InnerLoop.step() |
| `filesystem.py` | Directory initialization, config/checkpoint persistence |
| `overfit.py` | Training vs holdout score comparison |

## CLI Reference

```bash
# Seed initial population
factory outer-loop calibrate <project> \
  --benchmark featurebench \
  --population-size 4 \
  --project-dir /path/to/instance \
  --test-command "pytest tests/ -v"

# Evaluate a generation
factory outer-loop evaluate <project> --generation 0

# Run contrastive reflection
factory outer-loop reflect <project> --generation 0

# Produce next generation via mutation
factory outer-loop evolve <project> --generation 0

# Check convergence / show status
factory outer-loop status <project>
factory outer-loop status <project> --check-converge

# Promote winner to permanent mode
factory outer-loop promote <project> --mode evolve-gen0-a1b2c3d4
```

## E2E Validated Findings

From running the outer loop on FeatureBench instances (cancel-async-tasks, fix-code-vulnerability):

1. **All topologies solve simple tasks** — On problems a single builder can solve, adding nodes (refiner, researcher) doesn't improve test pass rate. Parsimony penalty makes simpler workflows score higher.
2. **Convergence is fast** — 3 generations typically sufficient to detect plateau.
3. **Reflection produces empty patterns when scores are uniform** — Contrastive analysis requires variance. On easy problems, all candidates score ~1.0 so there's nothing to contrast.
4. **Cost varies by topology** — Builder-only costs ~$1.10, builder+refiner ~$2.50, 3-node chains ~$3.00+. Simpler topologies are cheaper.
5. **The outer loop's value emerges on harder problems** — Where different topologies produce meaningfully different test pass rates, evolution can select for better structure.

## Data Layout

```
.factory/outer_loop/
├── config.json           # SwarmConfig (benchmark, population, target_project, test_command)
├── state.json            # OuterLoopState (generation, best_score, evaluations)
├── modes/                # Ephemeral mode JSONs
│   ├── evolve-gen0-a1b2c3d4.json
│   └── evolve-gen1-gen1_0.json
├── results/              # Per-generation evaluation results
│   ├── gen0.json
│   └── gen1.json
├── runs/                 # Per-candidate cycle summaries
│   └── evolve-gen0-a1b2c3d4/
│       └── cycle_summary.json
├── reflections/          # Contrastive reflection reports
├── events.jsonl          # Per-generation metrics
├── costs.jsonl           # Per-candidate cost tracking
└── trajectory.jsonl      # Score trajectory over generations
```

## Multi-Benchmark Support

The outer loop supports multiple benchmark types beyond FeatureBench. Each benchmark is defined as a TOML config file specifying the test format, test command, instance format, and seed workflow.

### Built-in Benchmarks

| Benchmark | Test Format | Instance Format | Description |
|-----------|-------------|-----------------|-------------|
| `featurebench` | `pytest` | `directory` | Feature implementation — partial credit scoring |
| `swebench` | `exit_code` | `git-repo` | SWE-bench bug fix — binary pass/fail |
| `aime` | `exact_match` | `question-answer` | AIME math competition — exact answer match |
| `forecastbench` | `json` | `question-answer` | ForecastBench — dynamic forecasting with Brier score |

### Test Output Formats

| Format | Parsing Method | Score Range |
|--------|---------------|-------------|
| `pytest` | Parse pytest stdout for pass/fail counts | 0.0–1.0 (fraction) |
| `exit_code` | Binary from subprocess returncode (0 = pass) | 0.0 or 1.0 |
| `json` | Extract metric via configurable JSON path | float |
| `exact_match` | Compare output to expected answer (optional regex) | 0.0 or 1.0 |

### Benchmark Config TOML Schema

```toml
[meta]
name = "my_benchmark"
description = "What this benchmark evaluates"

[test]
format = "json"                     # pytest | exit_code | json | exact_match
command = "python run_eval.py"      # shell command to run
timeout = 600                       # seconds
metric_path = "accuracy"            # for json format: dotted path to metric
answer_extraction = ""              # for exact_match: regex to extract answer

[instances]
format = "directory"                # directory | git-repo | question-answer
prep_command = "mkdir -p {instance_dir}/data"  # template variables: {instance_id}, {instance_dir}

[seed_workflow]
name = "improve"                    # workflow to use as seed

[scoring]
method = "metric_extraction"        # partial_credit | binary | metric_extraction | exact_match
```

### Adding a Custom Benchmark

1. Create a TOML file in one of these locations (searched in order):
   - Project-local: `.factory/benchmarks/my_bench.toml`
   - User-local: `~/.factory/benchmarks/my_bench.toml`
   - Built-in: `benchmarks/configs/my_bench.toml`

2. Run calibration with your benchmark:
   ```bash
   factory outer-loop calibrate /path/to/factory \
     --benchmark my_bench \
     --project-dir /path/to/instance
   ```

3. The outer loop auto-resolves `test_format`, `test_command`, and `instance_format` from your TOML. You can override any field via CLI flags.

### Working with Your Benchmark

Once registered, your benchmark integrates with the full outer loop pipeline:

**List available benchmarks:**
```bash
factory outer-loop list-benchmarks
```

**Calibrate with your benchmark:**
```bash
factory outer-loop calibrate /path/to/factory \
  --benchmark my_bench \
  --project-dir /path/to/instances \
  --population-size 4
```

**Override config settings via CLI:**
```bash
factory outer-loop calibrate /path/to/factory \
  --benchmark my_bench \
  --test-format json \
  --test-command 'python custom_eval.py' \
  --population-size 4
```

**Prepare instances from config:**
```bash
factory outer-loop prep-instances my_bench \
  --instances inst_001 inst_002 inst_003 \
  --output-dir /tmp/my-instances
```

The outer loop auto-resolves test_format, test_command, metric_path, instance_format, seed_workflow, and prep_command from your TOML config. CLI flags override any config value.

**Scoring formats:**
- `pytest`: Partial credit — passed/(passed+failed)
- `exit_code`: Binary — exit 0 = 1.0, non-zero = 0.0
- `json`: Extract any metric via dotted path (e.g. `stats.accuracy`, `brier_index`)
- `exact_match`: Compare stdout to expected_answer.txt (supports regex extraction)

### Instance Preparation

```bash
# List available benchmarks
factory outer-loop list-benchmarks

# Prepare instances from config
factory outer-loop prep-instances swebench \
  --instances django__django-12345 flask__flask-67890 \
  --output-dir /tmp/instances
```

The `prep_command` template supports `{instance_id}` and `{instance_dir}` variables. Validation runs after preparation:
- `directory`: checks directory exists
- `git-repo`: checks `.git/` exists and runs `git fsck --quick`
- `question-answer`: checks for `question.txt`/`question.md` and `answer.txt`/`expected.txt`

### CLI Flags

```bash
factory outer-loop calibrate <project> \
  --benchmark swebench \
  --test-format exit_code \         # override test format from TOML
  --test-command "pytest -xvs" \    # override test command
  --population-size 4
```

## EinsteinArena + Lumen Integration

EinsteinArena is a scientific optimization benchmark with RL-based training via the Lumen workflow. Each task directory contains an optimization problem with a verifier, instruction file, and default training config.

### Benchmark Config

The benchmark config lives at `benchmarks/configs/einsteinarena.toml`. It uses the `json` test format with `metric_extraction` scoring — the existing `JSONEvaluator` extracts the score via `metric_path = "score"`, so no custom evaluator class is needed.

### Dual-Mode Invocation

Lumen's preflight supports two invocation modes:

- **Mode A (explicit task):** `--task circle-packing` — the project path points to the factory root and the task is passed as an argument. This is the standard mode for interactive use.
- **Mode B (directory inference):** No `--task` argument — preflight infers the task from the project directory name (e.g., `/path/to/circle-packing`). This supports the outer loop's worktree isolation, where each instance directory is named after the task.

Both modes validate against the same `SUPPORTED_TASKS` list (tasks that have a `default_config.json`).

### Score Extraction

The score extractor (`benchmarks/einsteinarena/tools/extract_lumen_score.py`) scans `.factory/lumen/.running/iteration_*/evaluation_results.json` for the global best score and outputs:

```json
{"score": 0.85, "valid": true}
```

If no evaluation results exist (e.g., the Lumen workflow failed before producing any), it outputs `{"score": 0.0, "valid": false}`.

### Example: Calibrate

```bash
factory outer-loop calibrate /path/to/factory \
  --benchmark einsteinarena \
  --population-size 3 \
  --project-dir /path/to/benchmarks/einsteinarena/circle-packing
```

### Supported Tasks

Only tasks with `default_config.json` are supported:

| Task | Description |
|------|-------------|
| `circle-packing` | Pack circles to maximize density |
| `first-autocorrelation-inequality` | Optimize first autocorrelation bound |
| `second-autocorrelation-inequality` | Optimize second autocorrelation bound |
| `erdos-min-overlap` | Minimize overlap in Erdos problem |

### Known Limitations

- Lumen requires a GPU environment with vLLM, verl, and PyTorch — the outer loop evaluation nodes must have GPU access or run in `--mock` mode.
- Evaluation is sequential (one candidate at a time) in the current MVP. Parallel evaluation across GPU nodes is planned for v2.
- The score extractor reads only from `.factory/lumen/.running/` — it does not aggregate across multiple Lumen runs.
