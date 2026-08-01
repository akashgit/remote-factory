# Statefulness Eval Benchmark

Measures whether CEO session statefulness (via `session_summary.md`) improves
agent efficiency across consecutive improvement iterations.

## Design

The benchmark runs a controlled A/B experiment:

- **Treatment**: `session_summary.md` is preserved between iterations, giving the
  CEO context from prior runs
- **Control**: `session_summary.md` is deleted before each iteration, forcing a
  cold start every time

Each condition runs 5 iterations across 3 projects = 30 total CEO sessions.

## Metrics

Five metrics are extracted from Claude Code's `--output-format stream-json --verbose` output:

| Metric | What it measures | Expected direction |
|--------|-----------------|-------------------|
| `.factory/` read count | How many times the CEO reads `.factory/` files | Treatment should be lower (less re-reading) |
| Unique `.factory/` files read | Breadth of `.factory/` exploration | Treatment should be lower (targeted reads) |
| Agent re-invocations | `factory agent` calls via Bash | Treatment should show fewer redundant calls |
| Time to first meaningful action | Seconds from session start to first non-Read tool call | Treatment should be faster (skip orientation) |
| Total tool calls | Overall tool usage | Treatment should be lower (less wasted work) |

## How to Run

### Prerequisites

```bash
uv sync  # installs scipy + numpy as dev dependencies
```

### Run the benchmark

```bash
# Full suite (30 sessions, ~1 hour wall clock at 120s timeout each)
pytest benchmarks/statefulness-eval/test_statefulness.py -m slow -v

# Single project, single condition
pytest benchmarks/statefulness-eval/test_statefulness.py -m slow -v \
  -k "factory-ui and treatment"

# Single iteration (smoke test)
pytest benchmarks/statefulness-eval/test_statefulness.py -m slow -v \
  -k "factory-ui and treatment and iteration-1"
```

Results are saved to `.factory/experiments/statefulness/<project>/<condition>/iter-N.json`.

### Analyze results

```bash
uv run python benchmarks/statefulness-eval/analyze.py
```

Produces:
- `.factory/experiments/statefulness/analysis-report.md` — human-readable summary
- `.factory/experiments/statefulness/analysis-stats.json` — machine-readable stats

### Test the parser standalone

```bash
# Against prototype data
uv run python benchmarks/statefulness-eval/parse_tools.py \
  benchmarks/statefulness-eval/prototype-reference/fresh-eval/factory-ui/iter-1.jsonl

# Against any stream-JSON file
uv run python benchmarks/statefulness-eval/parse_tools.py /path/to/trace.jsonl
```

## Interpreting Results

### Effect sizes (Cohen's d)

| d value | Interpretation |
|---------|---------------|
| < 0.2 | Negligible — statefulness has no meaningful effect on this metric |
| 0.2–0.5 | Small — detectable but minor improvement |
| 0.5–0.8 | Medium — meaningful improvement worth pursuing |
| > 0.8 | Large — strong evidence that statefulness helps |

Negative d means treatment < control. For most metrics, negative d is the
desired direction (fewer reads, faster startup, fewer total calls).

### Bootstrap CI

The 95% confidence interval for the mean difference (treatment − control).
If the interval excludes zero, the effect is statistically reliable.

### Wilcoxon signed-rank test

Non-parametric paired test run per project. With n=5 pairs, statistical power
is limited — use primarily as a sanity check alongside Cohen's d.

## Architecture

```
benchmarks/statefulness-eval/
├── parse_tools.py          # Stream-JSON trace parser (TraceMetrics dataclass)
├── conftest.py             # Pytest fixtures (subprocess runner, result saver)
├── test_statefulness.py    # Parametrized test harness
├── analyze.py              # Statistical analysis (Cohen's d, Bootstrap CI, Wilcoxon)
├── README.md               # This file
└── prototype-reference/    # Preserved July 2026 prototype outputs (read-only)
    ├── fresh-eval/         # Stream-JSON traces with --verbose
    ├── build-mode-eval/    # Build-mode iteration traces
    └── agent-tracking-eval/# Original stdout-only logs
```

## Prototype Reference

The `prototype-reference/` directory contains preserved outputs from the July 29,
2026 prototype evaluation. These are historical artifacts — the new harness does
not use them. See `prototype-reference/README.md` for details on what was learned
from the prototype runs.
