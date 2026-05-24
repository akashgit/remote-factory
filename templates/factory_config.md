# Factory Configuration
<!-- This file configures the Remote Factory for your project. -->
<!-- The factory reads this during Init mode and generates .factory/config.json from it. -->
<!-- Fill in each section below. -->

## Goal
<!-- A single sentence describing what this project should achieve. -->

TODO: Describe the project goal here.

## Scope

### Modifiable
<!-- Files and directories the factory is allowed to create or edit. -->
<!-- One path per line. Glob patterns are supported. -->

- src/**/*.py
- tests/**/*.py

### Read-only
<!-- Files the factory may read but must never modify. -->

- README.md
- pyproject.toml

## Guards
<!-- Rules the factory must never violate. Checked before every commit. -->

- Do not delete or overwrite existing tests
- Do not modify files outside the declared scope
- Do not introduce secrets or credentials into the repository

## Eval

### Command
<!-- The shell command the factory runs to score a change. -->
<!-- It must output JSON to stdout matching the EvalResult format. -->

```bash
python eval/score.py
```

### Threshold
<!-- Minimum composite score (0.0-1.0) required to keep a change. -->

0.8

## Target Branch
<!-- Branch that experiment PRs target. Default: main -->
<!-- Set to a different branch (e.g. factory/dev) to stage factory changes before merging to main -->

main

## Project Eval
<!-- User-defined project-specific eval dimensions (benchmarks, accuracy, latency, etc.) -->
<!-- Each dimension starts with '- name:' followed by indented key: value lines -->
<!-- Output format: JSON with {"score": 0.0-1.0} or exit code (0=pass, non-zero=fail) -->
<!-- Example:
- name: benchmark_accuracy
  command: python eval/benchmark.py
  parse: json
  weight: 0.5
  timeout: 300
  description: Run benchmark suite and report accuracy
-->

## Eval Weights
<!-- Weight distribution across eval tiers (must sum to 1.0) -->
<!-- Only needed when Project Eval dimensions are defined -->
<!-- Default without project eval: hygiene 0.50, growth 0.50 -->
<!-- Default with project eval: hygiene 0.30, growth 0.20, project 0.50 -->
<!-- Example:
- hygiene: 0.25
- growth: 0.25
- project: 0.50
-->

## Hygiene Weights
<!-- Within-tier weight overrides for hygiene dimensions. -->
<!-- Only specify dimensions you want to change — unset dimensions keep defaults. -->
<!-- Weights are normalized within the tier, so they don't need to sum to 1.0. -->
<!-- Example:
- tests: 0.40
- coverage: 0.30
- lint: 0.10
-->

## Growth Weights
<!-- Within-tier weight overrides for growth dimensions. -->
<!-- Only specify dimensions you want to change — unset dimensions keep defaults. -->
<!-- Example:
- capability_surface: 0.30
- spec_compliance: 0.20
-->

## Smoke Test
<!-- Optional shell command that must pass before any change is kept. -->
<!-- If configured, this runs as part of `factory precheck` — failure = mandatory revert. -->
<!-- Use for e2e verification: hit an endpoint, run a CLI command, check a process starts. -->
<!-- Example:
```bash
curl -sf http://localhost:8000/health
```
-->

## Eval Spec
<!-- Testing instructions injected into the Evaluator's context. -->
<!-- Each item describes a qualitative check the Evaluator should perform. -->
<!-- Spec compliance is advisory — it does NOT affect the composite score. -->
<!-- Example:
- Run the CLI with --help and verify it prints usage info
- Start the dev server and confirm the landing page loads
- Verify the API returns JSON with the expected schema
-->

## Constraints
<!-- Soft rules that guide behavior but don't block commits. -->

- Prefer small, incremental changes over large rewrites
- Each change should be accompanied by at least one test
- Follow the existing code style and conventions

## Research Target
<!-- Only for research/benchmark projects. Define the metric to improve. -->
<!-- Example:
- objective: maximize SWE-bench resolve rate
- metric: resolved/total
- target: 0.35
- run_command: python run_benchmark.py
- result_path: results/output.json
- result_parser: json
- timeout: 3600
-->

## Mutable Surfaces
<!-- Files the Builder is allowed to modify during research experiments. -->
<!-- One glob pattern per line. Only used in research mode. -->
<!-- Example:
- src/**/*.py
- config/*.yaml
-->

## Fixed Surfaces
<!-- Ground truth files, test data, eval infrastructure. -->
<!-- These files are fingerprinted for leakage detection and MUST NOT be modified. -->
<!-- One glob pattern per line. Only used in research mode. -->
<!-- Example:
- tests/gold/*.json
- eval/**/*.py
- data/benchmark/*.jsonl
-->

## Research Constraints
<!-- Additional rules for the research loop. Only used in research mode. -->
<!-- Example:
- Do not use GPT-4 (cost constraint)
- Each experiment must complete within 30 minutes
-->

## Cost Budget
<!-- Per-cycle or total budget constraints for research experiments. -->
<!-- Example: $5/cycle, $50 total -->
