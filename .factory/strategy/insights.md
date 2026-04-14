# Cross-Project Insights — 2026-04-13

**3 projects**, **73 experiments**, **97% overall keep rate**

## Projects

- **cloud-gateway**: 6 experiments, 100% keep rate
- **locals-know**: 50 experiments, 96% keep rate
- **remote-factory**: 17 experiments, 100% keep rate

## Category Success Rates

| Category | Total | Kept | Rate |
|----------|-------|------|------|
| bugfix | 7 | 7 | 100% |
| observability | 11 | 11 | 100% |
| coverage | 5 | 5 | 100% |
| refactoring | 1 | 1 | 100% |
| performance | 1 | 0 | 0% |
| eval_improvement | 8 | 7 | 88% |
| agent_improvement | 3 | 3 | 100% |
| prompt_engineering | 1 | 1 | 100% |
| infrastructure | 2 | 2 | 100% |
| feature | 34 | 34 | 100% |

## Winning Strategies (>80% keep, 3+ experiments)

- **agent_improvement**: 100% keep rate (3 experiments)
- **bugfix**: 100% keep rate (7 experiments)
- **coverage**: 100% keep rate (5 experiments)
- **eval_improvement**: 88% keep rate (8 experiments)
- **feature**: 100% keep rate (34 experiments)
- **observability**: 100% keep rate (11 experiments)

## Patterns

### bugfix_reliable
_bugfix experiments have 100% keep rate across 3 projects (7 total)_
Confidence: 1.0

- cloud-gateway #Fix all ruff lint errors to bring lint s
- cloud-gateway #Fix mypy type check errors in gateway.py
- locals-know #Neighborhood-aware scoring: add favorite
- locals-know #HTMX Performance Hardening: Add debounci
- remote-factory #Fix 3 mypy errors in profile.py and runn

### coverage_reliable
_coverage experiments have 100% keep rate across 3 projects (5 total)_
Confidence: 1.0

- cloud-gateway #Add tests for uncovered exception paths 
- cloud-gateway #Boost test coverage for streaming and ki
- locals-know #Increase test coverage from 88% to 93%+ 
- locals-know #Add targeted tests for 4 untested scorin
- remote-factory #Increase test coverage above 80%

### observability_reliable
_observability experiments have 100% keep rate across 3 projects (11 total)_
Confidence: 1.0

- cloud-gateway #Add structured logging with request trac
- cloud-gateway #Add structured logging with request trac
- locals-know #Add logging to core business logic modul
- locals-know #Add logging to remaining uninstrumented 
- locals-know #Add logging to all remaining uninstrumen

### feature_reliable
_feature experiments have 100% keep rate across 2 projects (34 total)_
Confidence: 0.7

- locals-know #Browse page filtering and sorting: add H
- locals-know #Restaurant detail page -- similar restau
- locals-know #Search upgrade -- aggregate to restauran
- locals-know #Hours-aware Eat Now filtering: use store
- locals-know #Source attribution on quotes: show subre
