---
name: workflow-evolve
description: "Evolve mode — iterative code evolution via external MCP evaluation. Optimizes a single scalar metric by mutating code within EVOLVE-BLOCK boundaries and evaluating via an MCP server. Use when the project has an MCP evaluator configured and the user says 'evolve', 'optimize', or wants evolutionary code search on a benchmark."
disable-model-invocation: true
argument-hint: "<project_path> --mode evolve"
---

# Evolve Workflow

The user wants: **$ARGUMENTS**

**MCP Evaluation Mode:** This workflow evaluates code via an external MCP server, NOT via local tests/lint/types. The CEO must have access to the MCP tools `get_benchmark_info()` and `evaluate_solution()`. All code modifications MUST stay within EVOLVE-BLOCK-START/END markers.

## Step: Baseline

Initialize the baseline directory. The CEO must then:
1. Call get_benchmark_info(benchmark_name) via MCP — read the benchmark name from the ## Benchmark Target section in the CEO task
2. Write the initial program to .factory/baseline/initial.py
3. Call evaluate_solution(initial_program) via MCP to get baseline score
4. Write the eval result to .factory/baseline/eval.json
5. Write the current best code to .factory/evolve/current_best.py
6. Write the current score to .factory/evolve/current_score.json

```bash
python3 -c "import json; from pathlib import Path; p = Path('$PROJECT_PATH/.factory/baseline'); p.mkdir(parents=True, exist_ok=True); Path('$PROJECT_PATH/.factory/evolve').mkdir(parents=True, exist_ok=True); print('Baseline directory ready. CEO must call get_benchmark_info() and evaluate_solution() via MCP, then write initial.py and eval.json to .factory/baseline/.')"
```

## Phase 1: Researcher

```bash
factory agent researcher --task "Optimization technique research for code evolution. Read the initial program at .factory/baseline/initial.py. Identify EVOLVE-BLOCK-START/END markers to understand mutable regions. Analyze the algorithm structure, data representations, and constants. Search the web for optimization techniques relevant to the problem domain (extract domain from the benchmark name in .factory/baseline/eval.json). Read .factory/baseline/eval.json to identify the benchmark problem domain and its target metric. Based on the discovered domain, search for relevant optimization techniques, heuristics, and algorithmic strategies specific to that problem type. Read .factory/archive/ for prior knowledge on similar optimization problems. Write findings to .factory/strategy/research.md covering: code structure analysis (mutable vs fixed regions), candidate optimization techniques ordered by expected impact, parameter tuning opportunities, algorithmic alternatives.
Read: .factory/baseline/eval.json, .factory/baseline/initial.py
Write output to: .factory/strategy/research.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research.md`
3. Assess: Is the optimization research relevant to the problem domain? Does it identify the EVOLVE-BLOCK boundaries correctly? Are the proposed techniques ordered by expected impact? Are there at least 3 distinct approaches to try?
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `researcher` (max 3 iterations)*

## Phase 2: Strategist

```bash
factory agent strategist --task "Generate ONE code modification hypothesis for the evolve loop. Read research at .factory/strategy/research.md. Read the current best code at .factory/evolve/current_best.py. Read experiment history at .factory/results.tsv and .factory/experiments/. Read the current score from .factory/evolve/current_score.json. The hypothesis MUST be a specific code change within EVOLVE-BLOCK boundaries. Follow FEEC priority: Fix (bugs) > Exploit (tune parameters of proven approach) > Explore (new algorithm) > Combine (hybrid strategies). If the last 3 experiments were all reverted, note this — the CEO will trigger fresh research. Write a single hypothesis to .factory/strategy/current.md with: Category (algorithm-change|parameter-tuning|data-structure|initialization), Rationale, Modification (specific code), Expected Impact, Risk.
Read: .factory/evolve/current_best.py, .factory/evolve/current_score.json, .factory/strategy/research.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Strategy

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/current.md`
3. Assess: Review the code modification hypothesis. Check:
1) Is it a specific code change, not vague prose?
2) Does it target only EVOLVE-BLOCK regions?
3) Is the FEEC category correct?
4) Is the expected impact plausible?
5) Check stuck detection: if the last 3 experiments in .factory/results.tsv were all REVERT, trigger RELOOP to researcher for fresh perspective instead of proceeding to builder.
PROCEED if hypothesis is sound and not stuck. RELOOP to strategist if hypothesis is vague or wrong category. RELOOP to researcher if stuck (3 consecutive reverts).
4. Write verdict to `.factory/reviews/ceo-verdict-strategy.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max 3 iterations)*

## Step: Begin

Open a new experiment for the current hypothesis. The CEO must substitute $HYPOTHESIS with the hypothesis text.

```bash
factory begin $PROJECT_PATH --hypothesis "$HYPOTHESIS"
```

## Phase 3: Builder

```bash
factory agent builder --task "Apply the code modification hypothesis to produce a candidate program. Read the hypothesis at .factory/strategy/current.md. Read the current best code at .factory/evolve/current_best.py. CRITICAL CONSTRAINTS:
- ONLY modify code between EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers
- Preserve ALL code outside evolution markers (imports, helpers, return format)
- Maintain function signatures and return types expected by the evaluator
- No external dependencies beyond what's in the initial program
- Validate Python syntax (AST parse check)
Write the complete modified program to .factory/experiments/$EXP_ID/candidate.py. Also copy it to .factory/evolve/candidate.py for the evaluator.
Read: .factory/evolve/current_best.py, .factory/strategy/current.md
Write output to: .factory/evolve/candidate.py, .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Review builder output. Check:
1) candidate.py exists at .factory/evolve/candidate.py
2) Only EVOLVE-BLOCK regions were modified (diff the candidate against current_best.py)
3) Python syntax is valid
4) No external dependencies were added
REDIRECT to builder if constraints violated.
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 4: Health Checker

```bash
factory agent health_checker --task "Evaluate the candidate program via MCP and compare scores. 1. Read the candidate code from .factory/evolve/candidate.py
2. Call evaluate_solution(candidate_code) via MCP tool
3. Parse the evaluate_solution() response fields (combined_score, validity, eval_time, and any domain-specific metrics)
4. Read current best score from .factory/evolve/current_score.json
5. Read baseline eval_time from .factory/baseline/eval.json
6. Apply verdict logic:
   - If validity == false: REVERT ('Invalid solution')
   - If combined_score <= current_score: REVERT ('Score degraded or unchanged')
   - If eval_time > 10 * baseline_eval_time: REVERT ('Unacceptable slowdown')
   - Otherwise: KEEP ('Score improved')
7. Write eval results to .factory/experiments/$EXP_ID/eval_after.json
8. Write verdict with KEEP/REVERT and rationale to .factory/reviews/health-check.md
Include in the verdict: score_before, score_after, delta, validity, eval_time.
Read: .factory/baseline/eval.json, .factory/evolve/candidate.py, .factory/evolve/current_score.json
Write output to: .factory/reviews/health-check.md" --project "$PROJECT_PATH" --timeout 600
```

### CEO Review — Eval

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/health-check.md`
3. Assess: Review the evaluation verdict at .factory/reviews/health-check.md.
Read the Health Checker's KEEP/REVERT recommendation and rationale.
If KEEP:
  - Update .factory/evolve/current_best.py with the candidate code
  - Update .factory/evolve/current_score.json with the new score
  - Set $VERDICT=keep for finalize
If REVERT:
  - Keep current_best.py unchanged
  - Set $VERDICT=revert for finalize
Then PROCEED to finalize and archival.
4. Write verdict to `.factory/reviews/ceo-verdict-eval.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Step: Finalize

Close the experiment with a keep/revert verdict. The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS.

```bash
factory finalize $PROJECT_PATH --id $EXP_ID --verdict $VERDICT --hypothesis "$HYPOTHESIS"
```

## Phase 5: Archivist

```bash
factory agent archivist --task "Archive evolve experiment results and learnings. Read the experiment verdict at .factory/experiments/verdict.json. Read the hypothesis at .factory/strategy/current.md. Read the eval results at .factory/reviews/health-check.md. If KEEP: document what worked (algorithm insight, parameter sweet spot). If REVERT: document why it failed (validity issue, wrong assumption, local optimum). Write learnings to .factory/archive/experiments/$EXP_ID.md.
Read: .factory/experiments/verdict.json, .factory/reviews/health-check.md
Write output to: .factory/archive/experiment.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

### CEO Review — Convergence

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/evolve/current_score.json`
3. Assess: Check convergence criteria. Read .factory/evolve/current_score.json and .factory/results.tsv.
Exit (PROCEED) if ANY of:
  1. Target score reached (check factory.md convergence.target_score)
  2. Max cycles reached (check factory.md convergence.max_cycles, default 50)
  3. Diminishing returns: 5 consecutive cycles with improvement < 0.001
Continue (RELOOP to strategist) otherwise.
Log the convergence status: current_score, target, cycles_completed, recent_improvement_deltas.
4. Write verdict to `.factory/reviews/ceo-verdict-convergence.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max 3 iterations)*

## Phase 6: Archivist Final

```bash
factory agent archivist --task "Final evolution summary. Write a comprehensive summary of the evolution run: total experiments, keep/revert counts, score trajectory (baseline to final), best-performing hypothesis categories, key learnings. Read .factory/results.tsv for full history. Write to .factory/archive/evolve-summary.md.
Read: .factory/evolve/current_score.json
Write output to: .factory/archive/evolve-summary.md" --project "$PROJECT_PATH" --timeout 300 --model haiku
```
