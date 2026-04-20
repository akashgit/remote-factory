# Cross-Project Insights — 2026-04-20

**3 projects**, **66 experiments**, **100% overall keep rate**

## Projects

- **client-inquiry-response-agent-for-erica**: 5 experiments, 100% keep rate
- **remote-factory**: 25 experiments, 100% keep rate, score: 0.850
- **wxo**: 36 experiments, 100% keep rate

## Category Success Rates

| Category | Total | Kept | Rate |
|----------|-------|------|------|
| bugfix | 13 | 13 | 100% |
| observability | 8 | 8 | 100% |
| coverage | 3 | 3 | 100% |
| testing | 9 | 9 | 100% |
| refactoring | 1 | 1 | 100% |
| performance | 1 | 1 | 100% |
| eval_improvement | 5 | 5 | 100% |
| agent_improvement | 3 | 3 | 100% |
| prompt_engineering | 3 | 3 | 100% |
| infrastructure | 3 | 3 | 100% |
| feature | 17 | 17 | 100% |

## Winning Strategies (>80% keep, 3+ experiments)

- **agent_improvement**: 100% keep rate (3 experiments)
- **bugfix**: 100% keep rate (13 experiments)
- **coverage**: 100% keep rate (3 experiments)
- **eval_improvement**: 100% keep rate (5 experiments)
- **feature**: 100% keep rate (17 experiments)
- **infrastructure**: 100% keep rate (3 experiments)
- **observability**: 100% keep rate (8 experiments)
- **prompt_engineering**: 100% keep rate (3 experiments)
- **testing**: 100% keep rate (9 experiments)

## Patterns

### bugfix_reliable
_bugfix experiments have 100% keep rate across 3 projects (13 total)_
Confidence: 1.0

- client-inquiry-response-agent-for-erica #Fix lint: remove unused build_pipeline i
- client-inquiry-response-agent-for-erica #Fix mypy type errors: null guards, expli
- remote-factory #Fix 3 mypy errors in profile.py and runn
- remote-factory #Fix experiment state persistence and add
- remote-factory #Fix cmd_run and SKILL.md to use uv run

### observability_reliable
_observability experiments have 100% keep rate across 3 projects (8 total)_
Confidence: 1.0

- client-inquiry-response-agent-for-erica #Add structlog + request ID tracing to un
- client-inquiry-response-agent-for-erica #Add logging to all uncovered functions —
- remote-factory #Add factory study command to read intera
- remote-factory #Add structured logging to uninstrumented
- remote-factory #Add structlog logging to insights.py and

### feature_reliable
_feature experiments have 100% keep rate across 3 projects (17 total)_
Confidence: 1.0

- client-inquiry-response-agent-for-erica #Add Zillow/Redfin URL parsing to address
- remote-factory #Wire up Obsidian integration with factor
- remote-factory #Accept GitHub URL in factory run
- remote-factory #Add web search to factory study — Resear
- remote-factory #Dedicated factory Obsidian vault with pe

### eval_improvement_reliable
_eval_improvement experiments have 100% keep rate across 2 projects (5 total)_
Confidence: 0.7

- remote-factory #Rewrite factory evals with meaningful me
- remote-factory #Add sparklines to project cards + Chart.
- remote-factory #Add KPI summary strip with aggregate met
- remote-factory #Add score history line chart with hygien
- wxo #Add research documentation for experimen

### coverage_reliable
_coverage experiments have 100% keep rate across 2 projects (3 total)_
Confidence: 0.7

- remote-factory #Increase test coverage above 80%
- wxo #Add test coverage percentage to eval — r
- wxo #Add types.test.ts and use-sse.test.ts to

### infrastructure_reliable
_infrastructure experiments have 100% keep rate across 2 projects (3 total)_
Confidence: 0.7

- remote-factory #Add heartbeat loop to factory run — pers
- wxo #Add user feedback ACE optimization mode 
- wxo #Extract shared reflect-curate loop from 
