# Statefulness Eval — Analysis Report

## Events-Based Metrics (Primary)

These metrics come from `.factory/events.jsonl` and represent actual agent orchestration activity observed during each CEO session.

| Metric | Control (mean ± sd) | Treatment (mean ± sd) | Cohen's d | Effect | 95% CI |
|--------|--------------------|-----------------------|-----------|--------|--------|
| Agent Starts (events.jsonl) | 4.8 ± 2.8 | 5.1 ± 2.6 | 0.098 | negligible | [-1.60, 2.13] |
| Agent Completions (events.jsonl) | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Wall-Clock Duration (s) | 120.0 ± 0.0 | 120.0 ± 0.0 | -0.308 | small | [-0.03, 0.01] |

## Per-Project Wilcoxon Tests — Events Metrics

### factory-ui

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| Agent Starts (events.jsonl) | — | — | insufficient data |
| Agent Completions (events.jsonl) | — | — | insufficient data |
| Wall-Clock Duration (s) | 2.0 | 0.7500 | No |
### remote-factory-eval

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| Agent Starts (events.jsonl) | 1.0 | 1.0000 | No |
| Agent Completions (events.jsonl) | — | — | insufficient data |
| Wall-Clock Duration (s) | 5.0 | 0.6875 | No |
### remote-factory-timeout

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| Agent Starts (events.jsonl) | 1.0 | 1.0000 | No |
| Agent Completions (events.jsonl) | — | — | insufficient data |
| Wall-Clock Duration (s) | 4.0 | 0.8750 | No |

---

## Stream-JSON Metrics (Deprecated)

These metrics were parsed from stdout stream-JSON. Since `factory ceo` is a Python subprocess (not raw Claude Code), stdout is not structured JSONL — these are typically all zeros.

| Metric | Control (mean ± sd) | Treatment (mean ± sd) | Cohen's d | Effect | 95% CI |
|--------|--------------------|-----------------------|-----------|--------|--------|
| .factory/ Read Count | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Unique .factory/ Files Read | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Agent Re-invocations | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Time to First Meaningful Action (s) | — | — | — | insufficient data | — |
| Total Tool Calls | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |

## Per-Project Wilcoxon Tests — Stream Metrics

### factory-ui

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| .factory/ Read Count | — | — | insufficient data |
| Unique .factory/ Files Read | — | — | insufficient data |
| Agent Re-invocations | — | — | insufficient data |
| Time to First Meaningful Action (s) | — | — | insufficient data |
| Total Tool Calls | — | — | insufficient data |
### remote-factory-eval

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| .factory/ Read Count | — | — | insufficient data |
| Unique .factory/ Files Read | — | — | insufficient data |
| Agent Re-invocations | — | — | insufficient data |
| Time to First Meaningful Action (s) | — | — | insufficient data |
| Total Tool Calls | — | — | insufficient data |
### remote-factory-timeout

| Metric | W statistic | p-value | Significant (α=0.05) |
|--------|------------|---------|---------------------|
| .factory/ Read Count | — | — | insufficient data |
| Unique .factory/ Files Read | — | — | insufficient data |
| Agent Re-invocations | — | — | insufficient data |
| Time to First Meaningful Action (s) | — | — | insufficient data |
| Total Tool Calls | — | — | insufficient data |

## Interpretation Guide

- **Cohen's d**: < 0.2 negligible, 0.2–0.5 small, 0.5–0.8 medium, > 0.8 large
- **Bootstrap CI**: 95% confidence interval for mean difference (treatment − control)
- **Wilcoxon**: non-parametric paired test; p < 0.05 suggests significant difference
- Positive Cohen's d means treatment > control (more of that metric with statefulness)
