# Eval System

The Factory uses a three-tier composite scoring system to measure every change objectively. No change is kept without a measured improvement.

## Three Tiers

### Tier 1: Hygiene (6 dimensions)

Auto-detected from project tooling. These measure basic code quality:

| Dimension | What it checks | How |
|-----------|---------------|-----|
| `tests` | Test suite passes | Runs detected test command |
| `lint` | No lint errors | Runs detected linter (ruff, eslint, etc.) |
| `type_check` | Type checking passes | Runs mypy, pyright, tsc, etc. |
| `coverage` | Test coverage level | Parses coverage reports |
| `guard_patterns` | Guard rules respected | Checks scope, immutability rules |
| `config_parser` | `factory.md` is valid | Validates configuration |

Implementation: `factory/eval/hygiene.py`

### Tier 2: Growth (5 dimensions)

Computed by the factory itself. These measure whether the project is actually evolving:

| Dimension | What it measures | Weight |
|-----------|-----------------|--------|
| `capability_surface` | Modules, public functions, entry points | 0.28 |
| `experiment_diversity` | Variety of hypothesis categories attempted | 0.22 |
| `observability` | Logging, error handling, monitoring | 0.20 |
| `research_grounding` | Changes informed by research (vault, papers) | 0.16 |
| `factory_effectiveness` | Keep rate, score trajectory | 0.14 |

Implementation: `factory/eval/growth.py`

### Tier 3: Project Eval (user-defined)

Custom dimensions for domain-specific metrics. Defined in `factory.md`:

```markdown
## Project Eval
- name: benchmark_accuracy
  command: python eval/benchmark.py
  parse: json
  weight: 0.6
  timeout: 300
- name: inference_latency
  command: python eval/latency.py
  parse: exit_code
  weight: 0.4
```

Each command must output either:
- **json**: `{"score": 0.0-1.0}` to stdout (optionally `{"score": 0.85, "details": "..."}`)
- **exit_code**: Exit 0 for pass (score 1.0), non-zero for fail (score 0.0)

## Weight Distribution

| Scenario | Hygiene | Growth | Project |
|----------|---------|--------|---------|
| No project eval (default) | 50% | 50% | — |
| With project eval (default) | 30% | 20% | 50% |
| Custom (via `## Eval Weights`) | Configurable | Configurable | Configurable |

Configure in `factory.md`:

```markdown
## Eval Weights
- hygiene: 0.25
- growth: 0.25
- project: 0.50
```

## Scoring

The composite score is computed by `factory/eval/scorer.py`:

1. Each dimension produces a score (0.0 to 1.0) and a weight
2. Within each tier, scores are weighted and normalized
3. Tiers are combined using the weight distribution above
4. Guard violations force the composite to fail regardless of score
5. The threshold (default 0.8) determines keep/revert

## Guards

Guard rules are inviolable constraints checked via `factory/eval/guards.py`:

- **Scope guard**: Changes must be within `## Scope / Modifiable` patterns
- **Eval immutability**: The eval system itself cannot be modified by experiments

Guard failures override eval scores — a failing guard means mandatory revert.

## Precheck Gate

`factory precheck` runs 4 non-overridable checks before any keep/revert decision:

1. **Score direction** — score must not regress and must meet threshold
2. **Scope** — guard check must pass
3. **Anti-pattern** — hypothesis must not be >60% similar to a previously reverted one
4. **Smoke test** — if configured in `factory.md`, the smoke test command must pass

The CEO cannot override a failed precheck.

```bash
factory precheck ~/my-project \
    --score-before 0.7 \
    --score-after 0.85 \
    --hypothesis "add structured logging" \
    --baseline abc123
```

## Running Evals

```bash
# Full eval (all three tiers)
factory eval ~/my-project

# Skip project-specific eval (hygiene + growth only)
factory eval ~/my-project --skip-project-eval

# Check guards only
factory guard ~/my-project --baseline <sha>
```
