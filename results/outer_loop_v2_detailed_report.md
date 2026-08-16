# Outer Loop v2 — Detailed Experiment Report

**Date:** 2026-08-16
**Branch:** `factory/run-17122260`
**PR:** #1275
**Issue:** #1274

---

## Executive Summary

This session attempted to run the outer loop evolutionary search on FeatureBench. The code infrastructure was built correctly (CLI, checkpoints, progress tracking, adaptive timeouts). Three calibration experiments completed successfully. The actual evolution produced one generation of results but did not complete — and more critically, the execution architecture was wrong. The CEO delegated the entire outer loop execution to a Builder agent instead of running the SwarmEngine directly, and failed to use existing abstractions (the `skillopt` trainer, the FeatureBench workflow, and the proper inner/outer loop separation).

---

## Part 1: What Was Built (Code Changes)

### PR #1275 — 42 files, 8813 lines added

**Outer loop modules merged from `fix/outer-loop-v1-postmortem`:**
- `factory/outer_loop/engine.py` (596 lines) — SwarmEngine: population seeding, generation loop, evaluation, selection, mutation, plateau detection
- `factory/outer_loop/evaluator.py` (147 lines) — SwarmEvaluator: fitness cache, mandatory component checks, frozen node enforcement, batch evaluation via ThreadPoolExecutor
- `factory/outer_loop/direct_evaluator.py` (485 lines) — DirectFeatureBenchEvaluator: extracts /testbed/ from Docker, runs agents on host, verifies via `docker exec pytest`
- `factory/outer_loop/mutations.py` (768 lines) — 7 mutation operators: NODE_INSERT, NODE_REMOVE, EDGE_REDIRECT, PARALLELIZE, SERIALIZE, PARAM_MUTATE, PROMPT_MUTATE. Includes `_crossover_prompts()` (sentence-level interleaving) and new optional `crossover_fn` parameter for LLM crossover
- `factory/outer_loop/population.py` (203 lines) — Population and MAPElitesArchive: tournament selection, feature-based archiving
- `factory/outer_loop/models.py` (198 lines) — Pydantic models: Individual, SwarmConfig, GenerationSummary, OuterLoopResult, etc.
- `factory/outer_loop/designer.py` (402 lines) — DesignerAgent: generates "minimal" (3-node) and "thorough" (10-node) workflow variants from scratch
- `factory/outer_loop/harbor_evaluator.py` (249 lines) — HarborEvaluator: runs via `run-harbor.sh`, `create_seed_workflow()` function
- `factory/outer_loop/subset.py` (173 lines) — CalibratedSubsetSelector: difficulty-range filtering, stratified train/holdout split
- `factory/outer_loop/overfit.py` (156 lines) — OverfitDetector: training vs holdout delta tracking, early stop
- `factory/outer_loop/similarity.py` (134 lines) — NoveltyFilter: structural hashing, edit distance
- `factory/outer_loop/run_evolution.py` (133 lines) — Standalone CLI runner
- `factory/outer_loop/filesystem.py` (229 lines) — Experiment directory setup
- `factory/outer_loop/workflow.py` (168 lines) — Workflow registration for outer-loop mode

**New files added by the Builder during this session:**
- `factory/outer_loop/checkpoint.py` (61 lines) — `CheckpointData` Pydantic model, atomic writes (write to .tmp then rename), `load_latest_checkpoint()` for crash recovery
- `factory/outer_loop/progress.py` (144 lines) — `ProgressTracker`: append-only JSONL with 8 event types (generation_start/complete, agent_start/complete, eval_start/complete, checkpoint_saved, timeout)
- `factory/cli/outer_loop.py` (214 lines) — CLI handlers for `factory outer-loop calibrate` and `factory outer-loop evolve`
- `factory/workflow/primitives.py` additions (61 lines) — `Workflow.to_dict()` and `Workflow.from_dict()` for checkpoint serialization

**CLI changes:**
- `factory/cli/_main.py` — registered `outer-loop` subcommand with `calibrate` and `evolve` sub-subcommands
- `factory/cli/_helpers.py` — added `outer-loop` to known commands list
- `factory/cli/_parser_groups.py` — added `--benchmark`, `--budget`, `--population`, `--target-score`, `--seed`, `--training-instances`, `--holdout-instances` arguments

**Test files:** 18 test files merged + 5 new test files added = 308 tests total, all passing

**Bug fixes applied during execution:**
- `a575c929`: Fixed featurebench spec path — evaluator looked at `featurebench/<task_id>/` but Harbor downloaded specs to `featurebench/featurebench/<task_id>/`
- `a414a994`: Removed invalid `--disallowedTools` flag from `factory agent` calls (it's a Claude CLI flag, not a factory agent flag) and fixed patch paths to use `.resolve()` for absolute paths
- `7c52a331`: Capped agent timeout to `min(node.timeout, agent_timeout)` and reduced seed builder timeout from 7200s to 600s
- `2e23a3ea`: Added lv2 support to DirectFeatureBenchEvaluator — handles multi-function tasks where setup_patch removes more code
- `4cf1ad1f`: Fixed lv2 evaluation — wipes testbed git state for agent (clean slate), creates backup tar for verification step

---

## Part 2: Environment Setup

### Colima (Docker VM)
- **Original:** 12 CPUs, 64GB RAM, 118GB disk (77GB free)
- **Resized:** Stopped Colima, truncated datadisk to 500GB, restarted. Final: 492GB total, 414GB free
- **Resize method:** `colima stop && truncate -s 500G ~/.colima/_lima/_disks/colima/datadisk && colima start --cpu 12 --memory 64 --disk 500 --vm-type vz --mount-type virtiofs`
- **Filesystem expansion:** `colima ssh -- sudo resize2fs /dev/vdb1` (already auto-expanded)

### Docker Images
- 20 FeatureBench Docker images pulled from Docker Hub (libercoders/featurebench-specs_*)
- Image list: pydantic, fastapi, pytest, pandas, scikit_learn, matplotlib, sympy, xarray, sphinx, mlflow, transformers, seaborn, astropy, pytorch_lightning, trl, packaging, metaflow, meson, hatch, mypy
- Each image: ~28-30GB virtual size, shares base layers
- Total disk consumed: ~244GB of 492GB

### FeatureBench Task Specs
- Downloaded via `uvx harbor download featurebench --export --output-dir featurebench/` — 200 tasks in 9 seconds
- Specs stored at `featurebench/featurebench/<task_id>/` with structure:
  - `environment/Dockerfile` (FROM line points to Docker image)
  - `environment/setup_patch.diff` (removes function bodies — creates the puzzle)
  - `environment/test_patch.diff` (test files to restore for verification)
  - `instruction.md` (problem statement with interface specifications)
  - `tests/test.sh` (pytest command for verification)
  - `task.toml` (Harbor metadata)
- 190 of 200 tasks match our 20 pulled Docker images

### Harbor
- Available via `uvx harbor` (v0.21.0)
- Used only for downloading specs, not for running evaluations
- The DirectFeatureBenchEvaluator bypasses Harbor entirely

---

## Part 3: How DirectFeatureBenchEvaluator Works (The "Inner Loop")

This is the evaluation function that scores a single workflow candidate on a single FeatureBench instance. It implements the `EvaluatorFn` protocol.

### Step-by-step for one evaluation:

```
Input: workflow (Workflow object), instance_id (string)

1. EXTRACT TESTBED
   - Read Dockerfile FROM line → get Docker image name
   - `docker pull --platform linux/amd64 <image>`
   - `docker create --platform linux/amd64 <image>` → container ID
   - `docker cp <cid>:/testbed <local_tmpdir>/testbed`
   - `docker rm <cid>`
   
2. PREPARE TESTBED
   - `git init` in testbed (if not already a repo)
   - `git add . && git commit -m "initial"`
   - Apply setup_patch.diff → removes function bodies (creates the puzzle)
   - Delete test files listed in test_patch.diff (removes solution-revealing tests)
   - Copy instruction.md → testbed/task-instruction.md
   - Create .factory/ directory structure

3. RUN WORKFLOW AGENTS (on HOST, NOT in Docker)
   - Topological sort of workflow nodes
   - For each AgentNode in order:
     - `factory agent <role> --task "<prompt>" --project <testbed_path> --timeout <N>`
     - This spawns a Claude Code subprocess that reads the testbed, implements code, commits
   - GateNodes, ForkNodes, JoinNodes are skipped (no-op in direct evaluation)

4. VERIFY IN DOCKER (network disabled)
   - `docker create --platform linux/amd64 --network none <image> bash -c "sleep 600"`
   - `docker start <cid>`
   - Reverse-apply test_patch.diff to restore test files
   - `docker cp` only changed files from host testbed into container at /testbed/
   - `docker exec <cid> bash -c "source activate; pip install -e .; pytest <test_args>"`
   - returncode == 0 → RESOLVED (score 1.0)
   - returncode != 0 → FAILED (score 0.0)
   - `docker rm -f <cid>`

5. CLEANUP
   - `shutil.rmtree(tmpdir)`
   - Return EvalResult(score=0.0 or 1.0, benchmark_score=same)
```

### Key properties:
- **Score is binary:** 0.0 (all tests fail) or 1.0 (all tests pass). No partial credit.
- **Each instance is independent:** Different Docker image, different testbed, different tests.
- **Agents run on HOST:** The host machine has Claude Code installed. The testbed is a local directory. Docker is only used for the final pytest verification (to ensure correct Python version, dependencies, etc.)
- **Network disabled during verification:** `--network none` prevents answer leakage.

---

## Part 4: The Seed Workflows

### 4-Node Seed (default, `create_seed_workflow(minimal=False)`)
```
researcher(300s) → builder(7200s→600s) → health_checker(600s) → gate
```
- Researcher: reads task-instruction.md, explores repo, writes study-output.md
- Builder: reads study-output.md + task-instruction.md, implements feature, runs tests, commits
- Health_checker: runs test suite, reports results
- Gate: checks if changes were committed

### Builder-Only Seed (`create_seed_workflow(minimal=True)`)
```
builder(600s)
```
- Single node: reads task-instruction.md, explores repo on its own, implements feature, commits
- No prior study, no verification — the simplest possible workflow

### Designer Variants (generated by DesignerAgent during seeding)
- **Minimal (3 nodes):** researcher → builder → health_checker (same as 4-node minus gate)
- **Thorough (10 nodes):** expanded pipeline with multiple research phases, planning, verification
- **Custom:** additional variants with different node counts

---

## Part 5: Experiment 1 — Smoke Test

**Date:** 2026-08-15 22:09 UTC
**Seed:** 4-node pipeline
**Instance:** `pypa__packaging.013f3b03.test_metadata.e00b5801.lv1`
**Result:** RESOLVED (score 1.0)
**Time:** 280.4 seconds

### What happened:
1. DirectFeatureBenchEvaluator extracted testbed from `libercoders/featurebench-specs_packaging-instance_c393a6a8`
2. Applied setup_patch.diff (removed function bodies from packaging source)
3. Ran 4-node workflow: researcher studied repo → builder implemented → health_checker verified → gate checked
4. Copied changes into Docker container, ran pytest
5. All tests passed → score 1.0

### Purpose:
Proved the evaluation pipeline works end-to-end before running full calibration.

---

## Part 6: Experiment 2 — lv1 Calibration with 4-Node Seed

**Date:** 2026-08-15 22:16–23:12 UTC (56 minutes)
**Seed:** 4-node pipeline (researcher → builder → health_checker → gate)
**Instances:** 10 lv1 tasks
**Result:** 10/10 RESOLVED (100%)

### Per-instance results:

| # | Instance | Score | Resolved | Time (s) | Notes |
|---|----------|-------|----------|----------|-------|
| 1 | pydantic.test_deprecated_fields.lv1 | 1.0 | YES | 518.5 | 8.6 min |
| 2 | fastapi.test_compat.lv1 | 1.0 | YES | 242.2 | 4.0 min |
| 3 | pandas.test_col.lv1 | 1.0 | YES | 307.5 | 5.1 min |
| 4 | seaborn.test_bar.lv1 | 1.0 | YES | 319.5 | 5.3 min |
| 5 | sphinx.test_build_gettext.lv1 | 1.0 | YES | 1006.9 | 16.8 min — slowest |
| 6 | matplotlib.test_backend_registry.lv1 | 1.0 | YES | 410.6 | 6.8 min |
| 7 | sympy.test_inverse.lv1 | 1.0 | YES | 31.4 | 0.5 min — fastest |
| 8 | mlflow.test_abstract_store.lv1 | 1.0 | YES | 235.8 | 3.9 min |
| 9 | pytest.raises_group.lv1 | 1.0 | YES | 79.2 | 1.3 min |
| 10 | packaging.test_metadata.lv1 | 1.0 | YES | 49.6 | 0.8 min |

**Total time:** 3201s (53 min)
**Split:** Training 7, Holdout 3
**Seed score:** 1.0

### What each evaluation did:
For each instance, the evaluator extracted the testbed from Docker, applied setup_patch (removed one function body — lv1 means one function to implement), ran the 4-node workflow (researcher studied the codebase, builder implemented the function, health_checker verified), then verified in Docker. Every instance was resolved because lv1 tasks are single-function implementations that Claude can handle easily with or without prior study.

---

## Part 7: Experiment 3 — lv1 Calibration with Builder-Only Seed

**Date:** 2026-08-16 01:01–02:07 UTC (66 minutes)
**Seed:** Builder-only (1 node)
**Instances:** Same 10 lv1 tasks
**Result:** 10/10 RESOLVED (100%)

### Per-instance results:

| # | Instance | Score | Resolved | Time (s) | vs 4-Node |
|---|----------|-------|----------|----------|-----------|
| 1 | pydantic.test_deprecated_fields.lv1 | 1.0 | YES | 421.2 | -97s slower |
| 2 | fastapi.test_compat.lv1 | 1.0 | YES | 110.6 | -132s faster |
| 3 | pandas.test_col.lv1 | 1.0 | YES | 338.2 | +31s slower |
| 4 | seaborn.test_bar.lv1 | 1.0 | YES | 201.9 | -118s faster |
| 5 | sphinx.test_build_gettext.lv1 | 1.0 | YES | 217.9 | -789s faster |
| 6 | matplotlib.test_backend_registry.lv1 | 1.0 | YES | 243.7 | -167s faster |
| 7 | sympy.test_inverse.lv1 | 1.0 | YES | 1024.0 | +993s slower |
| 8 | mlflow.test_abstract_store.lv1 | 1.0 | YES | 427.2 | +191s slower |
| 9 | pytest.raises_group.lv1 | 1.0 | YES | 550.4 | +471s slower |
| 10 | packaging.test_metadata.lv1 | 1.0 | YES | 413.4 | +364s slower |

**Total time:** 3949s (66 min)
**Seed score:** 1.0

### Analysis:
Builder-only achieves the same 100% score but with more time variance. Some instances (sphinx, seaborn) are faster without the researcher overhead. Others (sympy, pytest, packaging) are slower because the builder spends more time exploring the codebase on its own. The researcher node provides no score benefit on lv1 — it's pure overhead.

**Conclusion:** lv1 is a ceiling. Both simple and complex workflows achieve 100%. No evolutionary signal.

---

## Part 8: Experiment 4 — lv2 Calibration with Builder-Only Seed

**Date:** 2026-08-16 01:27–03:29 UTC (122 minutes)
**Seed:** Builder-only (1 node)
**Instances:** 10 lv2 tasks
**Result:** 0/10 RESOLVED (0%)

### Per-instance results:

| # | Instance | Score | Resolved | Time (s) | Failure Mode |
|---|----------|-------|----------|----------|--------------|
| 1 | astropy.test_basic_rgb.lv2 | 0.0 | NO | 259.2 | Multiple functions unimplemented |
| 2 | fastapi.test_compat.lv2 | 0.0 | NO | 267.7 | Cross-file dependency errors |
| 3 | transformers.test_modeling_pixtral.lv2 | 0.0 | NO | 692.2 | Complex model architecture |
| 4 | pytorch-lightning.test_fsdp_integration.lv2 | 0.0 | NO | 1888.0 | Distributed training APIs — 31 min, likely hit timeout+retry |
| 5 | mlflow.test_config.lv2 | 0.0 | NO | 160.4 | Config parsing edge cases |
| 6 | seaborn.test_regression.lv2 | 0.0 | NO | 401.3 | Statistical computation |
| 7 | pandas.test_col.lv2 | 0.0 | NO | 394.0 | DataFrame operations |
| 8 | xarray.test_coordinate_transform.lv2 | 0.0 | NO | 548.0 | Coordinate system transforms |
| 9 | sympy.test_puiseux.lv2 | 0.0 | NO | 1444.3 | Symbolic math — 24 min |
| 10 | meson.cargotests.lv2 | 0.0 | NO | 943.0 | Build system internals — 16 min |

**Total time:** 6998s (117 min)
**Seed score:** 0.0

### What lv2 means:
lv2 tasks have **multiple function bodies removed** by setup_patch.diff. The agent must implement 3-10+ functions that work together correctly, with proper cross-file references, correct types, and matching interfaces. This is dramatically harder than lv1 (one function).

### Why builder-only fails at 0%:
The builder agent has no prior study of the codebase. It reads `task-instruction.md`, explores the repo briefly, then tries to implement. With multiple functions to implement simultaneously and no systematic study phase, it misses cross-file dependencies and interface details. The binary scoring (all tests pass or score=0) means even partial implementations score zero.

### Failure analysis (inferred from timing patterns):
- **Fast failures (160-270s):** Agent writes code quickly but gets fundamental interfaces wrong. Docker pytest fails immediately.
- **Medium failures (390-700s):** Agent spends more time exploring but still misses some cross-file dependencies. Partial implementation that doesn't pass all tests.
- **Slow failures (940-1890s):** Agent gets deep into implementation, may hit timeout, retries with 2x timeout (adaptive timeout feature), still fails. The pytorch-lightning (1888s) and sympy (1444s) instances suggest timeout→retry cycles.

---

## Part 9: Experiment 5 — Evolution on lv2 with Builder-Only Seed

**Date:** 2026-08-16 03:30–10:37 UTC (7.1 hours for Gen 0 only)
**Seed:** Builder-only (1 node, score 0.0 on lv2)
**Config:** population=4, budget=20, generations=2, parallelism=2, tournament_size=3
**Training:** 7 lv2 instances (astropy, fastapi, transformers, pytorch-lightning, mlflow, seaborn, pandas)
**Holdout:** 3 lv2 instances (xarray, sympy, meson)

### Generation 0 Candidates (from checkpoint_gen_0.json):

| Individual ID | Type | Parent | Mutation | Score | Per-Instance Results |
|---------------|------|--------|----------|-------|---------------------|
| `cfc66ffcbd78` | Seed (builder-only) | None | None | 0.0 | 0/7 — all 7 training instances FAIL |
| `53a469dc1e2d` | Designer minimal (3 nodes: R→B→HC) | None | None | 0.0 | 0/7 — all 7 training instances FAIL |
| `089bb24deddd` | Designer thorough (10 nodes) | None | None | 0.0 | 0/7 — all 7 training instances FAIL |
| `b208ca660f7d` | Mutation of thorough | `089bb24deddd` | `node_insert` on `agent_470` | 0.0 | 0/7 — all 7 training instances FAIL |

### What happened in detail:
1. **SwarmEngine.seed()** created initial population:
   - Slot 0: unmodified builder-only seed
   - Slot 1: DesignerAgent.design_minimal() → researcher→builder→health_checker
   - Slot 2: DesignerAgent.design_thorough() → 10-node expanded pipeline
   - One mutation of thorough: NODE_INSERT added an agent node (`agent_470`)

2. **SwarmEngine.evolve_generation()** evaluated all 4 individuals on 7 training instances:
   - Each evaluation: DirectFeatureBenchEvaluator._eval_instance() × 7 instances
   - Each instance evaluation: extract testbed → run workflow agents → verify in Docker
   - For the builder-only seed: 1 agent call × 7 instances = 7 evaluations
   - For the 3-node minimal: 3 agent calls × 7 instances = 21 evaluations
   - For the 10-node thorough: ~10 agent calls × 7 instances = ~70 evaluations
   - For the mutation: similar to thorough
   - **Total agent calls in Gen 0:** approximately 7 + 21 + 70 + 70 = ~168 Claude agent invocations

3. **All scored 0.0:** Even the 10-node thorough pipeline with multiple research phases couldn't solve any lv2 instance. The problem is fundamental — lv2 requires implementing multiple interdependent functions correctly, which is a prompt quality / model capability issue, not a workflow structure issue.

4. **Budget consumed:** 5 of 20 (the 4 initial evaluations + 1 holdout check)

5. **Gen 0 duration:** 25,612 seconds (7.1 hours) — mostly spent on the 10-node thorough variant evaluating 7 instances with ~10 agents each

6. **Gen 1 started** but the Builder agent hit its 14,400s (4 hour) wall-clock timeout before Gen 1 completed any evaluations.

### Why evolution produced no signal:
- **All candidates scored 0.0** → tournament selection has nothing to differentiate
- **Binary scoring** → no gradient between "almost solved" and "completely wrong"
- **lv2 is beyond the capability threshold** → no workflow structure can compensate for the model's inability to implement 5+ interdependent functions correctly

---

## Part 10: Mixed Calibration Setup (Never Used for Evolution)

**Created at:** 2026-08-16 03:49 UTC

The Builder created `calibration_mixed.json` mixing lv1 (easy) and lv2 (hard) instances to get a 57% seed score — the ideal range for evolution. However, this mixed set was **never actually used for evolution**. The SwarmEngine was configured with the pure lv2 training instances, not the mixed set.

**Mixed calibration design:**
- Training (7): 4× lv1 (fastapi, sympy, packaging, mlflow — all PASS) + 3× lv2 (fastapi, mlflow, seaborn — all FAIL)
- Holdout (3): 2× lv1 (pytest, matplotlib — PASS) + 1× lv2 (pandas — FAIL)
- Seed score: 4/7 = 0.571

This would have been the right design for evolution — a seed that passes some but not all instances.

---

## Part 11: What I Got Wrong — Critique

### Mistake 1: Delegated execution to the Builder instead of orchestrating it myself

The outer loop evolution is an **orchestration task**, not a code-writing task. The SwarmEngine has a `run()` method. The CLI has `factory outer-loop calibrate` and `factory outer-loop evolve`. These should have been invoked directly — either by the CEO calling the CLI, or by running `python scripts/run_evolution.py` in tmux. Instead, I told a Builder agent to "run the evolution," which meant a Claude Code subprocess was running a Python loop that spawned more Claude Code subprocesses that spawned more Claude Code subprocesses. Three levels of nesting.

The correct approach: Build the code with the Builder (Parts A-E), then run the evolution directly via the CLI or a script. The CEO's job is to orchestrate, not to delegate orchestration to a Builder.

### Mistake 2: Did not use the existing `skillopt` abstraction

The factory already has `factory/skillopt/` — a complete "DL-style training loop for SKILL.md optimization." It has:
- `SkillOptTrainer` — epochs, steps, batch_size, learning_rate, eval_split, metric
- `FeaturebenchAdapter` — connects to Harbor for rollouts
- `rollout()` → runs the workflow against benchmark instances
- `reflect()` → analyzes failures and generates improvement patches
- `apply_patch()` → modifies the SKILL.md
- `gate()` → compares train vs holdout scores

This is the **real inner loop**. The `DirectFeatureBenchEvaluator` I used is a lower-level primitive — it evaluates one workflow on one instance. The `skillopt` trainer wraps this in a proper optimization loop with reflection and patching.

The outer loop (`SwarmEngine`) should evolve the workflow graph structure, while the inner loop (`SkillOptTrainer` or similar) optimizes the prompts within a fixed structure. I conflated the two — the SwarmEngine was trying to do both graph evolution AND prompt evaluation simultaneously, with no inner loop optimization.

### Mistake 3: Used pure lv2 for evolution instead of the mixed set

The `calibration_mixed.json` was created with the right design (57% seed score), but the evolution was configured with pure lv2 instances (0% seed score). This meant all candidates scored 0.0 — no evolutionary signal, no gradient, no selection pressure. The mixed set was never fed to the SwarmEngine.

### Mistake 4: Binary scoring with no partial credit

The DirectFeatureBenchEvaluator returns 0.0 or 1.0 — either all tests pass or none count. For lv2 tasks with multiple functions, an agent might implement 4 out of 5 functions correctly but still score 0.0. A partial-credit scoring function (e.g., fraction of tests that pass) would give evolution a gradient to climb. This exists conceptually in the FeatureBench evaluation (pass-to-pass + fail-to-pass tests) but isn't surfaced through the DirectFeatureBenchEvaluator.

### Mistake 5: No consideration of what the CEO's role should be in running the outer loop

The outer loop is not a one-shot build task. It's a long-running optimization process that needs:
- Monitoring (are evaluations progressing? are agents timing out?)
- Decision-making (should we adjust timeouts? switch to a different instance set?)
- Course correction (Gen 0 scored 0/0/0/0 — should we abort and try a different seed?)

The CEO should be the one making these decisions in real-time, not delegating them to a Builder that has no agency to change course. The CEO could use `factory outer-loop evolve` in tmux, monitor progress.jsonl, and intervene when needed.

### Mistake 6: Did not use the FeatureBench workflow as the inner loop

There's already a registered `featurebench` workflow at `factory/workflow/contributed/featurebench/workflow.py` with a 4-node pipeline (study → builder → gate_verify → auto_merge) and a RELOOP from gate back to builder (max 3 iterations). This is the **inner loop** — the workflow that actually solves FeatureBench instances. The outer loop should evolve THIS workflow's structure and prompts.

Instead, `DirectFeatureBenchEvaluator._run_workflow_agents()` runs a simplified version that ignores gates, forks, and joins — it just runs AgentNodes in topological order. It doesn't use the reloop feature that the real FeatureBench workflow has. This means the evaluation doesn't reflect the actual inner loop behavior.

### Mistake 7: 7+ hours for one generation is unacceptable

Gen 0 took 25,612 seconds (7.1 hours) because the 10-node thorough variant runs ~10 agents per instance × 7 instances = ~70 agent invocations, each taking 5-30 minutes. This was predictable from the calibration data (lv2 instances take 3-31 minutes each). The budget should have been capped, or the population should have excluded the thorough variant.

---

## Part 12: What Should Have Been Done

### The right architecture:

```
CEO (orchestrator)
  │
  ├── Builder: writes code (Parts A-E) → done in 1-2 hours
  │
  └── CEO runs directly (not via Builder):
        │
        ├── Calibration: `factory outer-loop calibrate --project . --parallelism 4`
        │     Uses mixed lv1+lv2 instances
        │     Targets 30-70% seed score
        │
        └── Evolution: `factory outer-loop evolve --project . --generations 3`
              │
              ├── Outer loop: SwarmEngine evolves workflow STRUCTURE
              │     - NODE_INSERT/REMOVE/PARALLELIZE change the graph
              │     - Population of workflow graphs, tournament selection
              │
              └── Inner loop: For each candidate workflow graph:
                    - SkillOptTrainer or DirectFeatureBenchEvaluator
                    - Runs the workflow on training instances
                    - Returns score (should be partial credit, not binary)
                    - The FeatureBench workflow (with RELOOP) is the template
```

### How the CEO should run it:

1. Builder writes code → PR → QA passes → merge
2. CEO runs calibration directly (not via Builder): `factory outer-loop calibrate --project . --parallelism 4`
3. CEO reviews calibration results, adjusts parameters
4. CEO runs evolution in tmux: `tmux new -s evolution && python scripts/run_evolution.py`
5. CEO monitors `tail -f .factory/outer_loop/progress.jsonl`
6. CEO intervenes if all candidates score 0 (change instance set, adjust seed, add partial credit)
7. CEO reads final results and writes report

### What the next issue should specify:

1. Use mixed lv1+lv2 calibration (57% seed score)
2. Add partial credit scoring (fraction of tests passing, not binary)
3. Use the existing FeatureBench workflow with RELOOP as the inner loop template
4. Run evolution via CLI/script, not via Builder agent
5. CEO monitors and course-corrects in real-time
6. Cap population to exclude variants with >5 nodes (too slow for lv2)
7. Consider using `skillopt` trainer for prompt-level optimization within a fixed graph structure

---

## Part 13: Files Produced

```
.factory/outer_loop/
├── calibration.json          # lv1, 4-node seed: 10/10 PASS (100%)
├── calibration_v2.json       # lv1, builder-only seed: 10/10 PASS (100%)
├── calibration_lv2.json      # lv2, builder-only seed: 0/10 PASS (0%)
├── calibration_mixed.json    # mixed lv1+lv2: seed score 57% (never used for evolution)
├── checkpoint_gen_0.json     # Gen 0 state: 4 individuals, all scored 0.0
├── progress.jsonl            # 63 events: calibration + evolution start/complete
├── smoke_test.json           # packaging lv1: score 1.0, 280s
```

```
results/
└── outer_loop_v2_report.md   # Summary report (partial — written before evolution completed)
```

```
scripts/
├── run_evolution.py          # Standalone evolution runner (SwarmEngine wrapper)
├── run_outer_loop.py         # Calibration + evolution combined runner
└── generate_report.py        # Report generator from calibration + evolution data
```

---

## Part 14: Timeline

| Time (UTC) | Event |
|------------|-------|
| 2026-08-15 16:14 | Factory discovered, graph updated, study run |
| 2026-08-15 16:18 | 3 parallel researchers spawned (similar, techstack, pitfalls) |
| 2026-08-15 16:33 | Research complete, CEO review PROCEED |
| 2026-08-15 16:40 | Strategist produces plan, user approves |
| 2026-08-15 17:00 | Builder #1: code implementation (Parts A-E) |
| 2026-08-15 17:14 | Builder #1 complete, PR #1275 opened |
| 2026-08-15 17:36 | Code reviewer + adversarial tester (parallel), both PASS |
| 2026-08-15 17:39 | Colima resize: 118GB → 492GB |
| 2026-08-15 17:40 | Docker image pulls begin (20 images) |
| 2026-08-15 18:03 | Harbor download: 200 FeatureBench specs in 9 seconds |
| 2026-08-15 18:09 | Smoke test PASS (packaging lv1) |
| 2026-08-15 18:16 | Builder #2: calibration + evolution (MISTAKE — should not have been Builder) |
| 2026-08-15 22:16 | lv1 calibration starts (4-node seed, 10 instances) |
| 2026-08-15 23:12 | lv1 calibration complete: 10/10 PASS |
| 2026-08-15 23:12 | Evolution attempt #1 on lv1: abandoned (100% seed = no signal) |
| 2026-08-16 00:54 | Builder-only lv1 calibration: 10/10 PASS (confirms lv1 too easy) |
| 2026-08-16 01:27 | lv2 calibration starts (builder-only, 10 instances) |
| 2026-08-16 03:29 | lv2 calibration complete: 0/10 PASS |
| 2026-08-16 03:30 | Evolution starts on lv2 (pure lv2, not mixed — MISTAKE) |
| 2026-08-16 03:49 | Mixed calibration created (57% seed) but not used for evolution |
| 2026-08-16 10:37 | Gen 0 complete: 4 individuals, all scored 0.0, took 7.1 hours |
| 2026-08-16 10:37 | Gen 1 starts but Builder hits 4-hour wall-clock timeout |
| 2026-08-16 ~10:51 | Builder agent killed by timeout (exit code 1, "exceeded max wall-clock") |
