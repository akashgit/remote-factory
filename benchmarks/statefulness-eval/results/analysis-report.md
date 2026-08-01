# Statefulness Eval — Analysis Report

## Overall Results

| Metric | Control (mean ± sd) | Treatment (mean ± sd) | Cohen's d | Effect | 95% CI |
|--------|--------------------|-----------------------|-----------|--------|--------|
| .factory/ Read Count | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Unique .factory/ Files Read | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Agent Re-invocations | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |
| Time to First Meaningful Action (s) | — | — | — | insufficient data | — |
| Total Tool Calls | 0.0 ± 0.0 | 0.0 ± 0.0 | 0.000 | negligible | [0.00, 0.00] |

## Per-Project Wilcoxon Signed-Rank Tests

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
