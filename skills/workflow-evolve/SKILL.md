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


<!-- node: FnNode id=baseline -->
<!-- command: python3 -c "import json; from pathlib import Path; p = Path('{project_path}/.factory/baseline'); p.mkdir(parents=True, exist_ok=True); Path('{project_path}/.factory/evolve').mkdir(parents=True, exist_ok=True); print('Baseline directory ready. CEO must call get_benchmark_info() and evaluate_solution() via MCP, then write initial.py and eval.json to .factory/baseline/.')" -->
<!-- reads: none -->
<!-- writes: .factory/baseline/eval.json, .factory/baseline/initial.py, .factory/evolve/current_best.py, .factory/evolve/current_score.json, .factory/experiments/000/eval_before.json -->
<!-- edges: unconditional → researcher -->

Initialize the baseline directory. The CEO must then:
1. Call get_benchmark_info() via MCP to retrieve the initial program
2. Write the initial program to .factory/baseline/initial.py
3. Call evaluate_solution(initial_program) via MCP to get baseline score
4. Write the eval result to .factory/baseline/eval.json
5. Write the current best code to .factory/evolve/current_best.py
6. Write the current score to .factory/evolve/current_score.json
7. Copy the eval result to .factory/experiments/000/eval_before.json (same content as baseline/eval.json — enables CycleAnalyzer artifact discovery)
8. Emit eval.completed event to .factory/events.jsonl with the baseline composite score

```bash
python3 -c "import json; from pathlib import Path; p = Path('$PROJECT_PATH/.factory/baseline'); p.mkdir(parents=True, exist_ok=True); Path('$PROJECT_PATH/.factory/evolve').mkdir(parents=True, exist_ok=True); print('Baseline directory ready. CEO must call get_benchmark_info() and evaluate_solution() via MCP, then write initial.py and eval.json to .factory/baseline/.')"
```

## Phase 1: Researcher


<!-- node: AgentNode id=researcher role=researcher blocking=true -->
<!-- reads: .factory/baseline/eval.json, .factory/baseline/initial.py -->
<!-- writes: .factory/strategy/research.md -->
<!-- edges: unconditional → gate_research -->

```bash
factory agent researcher --task "Optimization technique research for code evolution. Read the initial program at .factory/baseline/initial.py. Identify EVOLVE-BLOCK-START/END markers to understand mutable regions. Analyze the algorithm structure, data representations, and constants. Search the web for optimization techniques relevant to the problem domain (extract domain from the benchmark name in .factory/baseline/eval.json). Read .factory/baseline/eval.json to identify the benchmark problem domain and its target metric. Based on the discovered domain, search for relevant optimization techniques, heuristics, and algorithmic strategies specific to that problem type. Read .factory/archive/ for prior knowledge on similar optimization problems. Write findings to .factory/strategy/research.md covering: code structure analysis (mutable vs fixed regions), candidate optimization techniques ordered by expected impact, parameter tuning opportunities, algorithmic alternatives.
Read: .factory/baseline/eval.json, .factory/baseline/initial.py
Write output to: .factory/strategy/research.md}}" --project "$PROJECT_PATH" --timeout {{timeout_researcher::600}}
```

<!-- gate: GateNode id=gate_research evaluator_type=agent evaluator_role=ceo -->
<!-- reads: .factory/strategy/research.md -->
<!-- edges: proceed → strategist, reloop → researcher -->

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research.md`
3. Assess: {{gate_prompt_gate_research::Is the optimization research relevant to the problem domain? Does it identify the EVOLVE-BLOCK boundaries correctly? Are the proposed techniques ordered by expected impact? Are there at least 3 distinct approaches to try?}}
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `researcher` (max {{max_iterations_gate_research::3}} iterations)*

## Phase 2: Strategist


<!-- node: AgentNode id=strategist role=strategist blocking=true -->
<!-- reads: .factory/evolve/current_best.py, .factory/evolve/current_score.json, .factory/strategy/research.md -->
<!-- writes: .factory/strategy/current.md -->
<!-- edges: unconditional → gate_strategy -->

```bash
factory agent strategist --task "{{task_prompt_strategist::Generate ONE code modification hypothesis for the evolve loop. Read research at .factory/strategy/research.md. Read the current best code at .factory/evolve/current_best.py. Read experiment history at .factory/results.tsv and .factory/experiments/. Read the current score from .factory/evolve/current_score.json. The hypothesis MUST be a specific code change within EVOLVE-BLOCK boundaries. Follow FEEC priority: Fix (bugs) > Exploit (tune parameters of proven approach) > Explore (new algorithm) > Combine (hybrid strategies). If the last 3 experiments were all reverted, note this — the CEO will trigger fresh research. Write a single hypothesis to .factory/strategy/current.md with: Category (algorithm-change|parameter-tuning|data-structure|initialization), Rationale, Modification (specific code), Expected Impact, Risk.
Read: .factory/evolve/current_best.py, .factory/evolve/current_score.json, .factory/strategy/research.md
Write output to: .factory/strategy/current.md}}" --project "$PROJECT_PATH" --timeout {{timeout_strategist::600}}
```

<!-- gate: GateNode id=gate_strategy evaluator_type=agent evaluator_role=ceo -->
<!-- reads: .factory/strategy/current.md -->
<!-- edges: proceed → begin, reloop → strategist -->

### CEO Review — Strategy

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/current.md`
3. Assess: {{gate_prompt_gate_strategy::Review the code modification hypothesis. Check:
1) Is it a specific code change, not vague prose?
2) Does it target only EVOLVE-BLOCK regions?
3) Is the FEEC category correct?
4) Is the expected impact plausible?
5) Check stuck detection: if the last 3 experiments in .factory/results.tsv were all REVERT, trigger RELOOP to researcher for fresh perspective instead of proceeding to builder.
PROCEED if hypothesis is sound and not stuck. RELOOP to strategist if hypothesis is vague or wrong category. RELOOP to researcher if stuck (3 consecutive reverts).}}
4. Write verdict to `.factory/reviews/ceo-verdict-strategy.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max {{max_iterations_gate_strategy::3}} iterations)*

## Step: Begin


<!-- node: FnNode id=begin -->
<!-- command: factory begin {project_path} --hypothesis "$HYPOTHESIS" -->
<!-- reads: none -->
<!-- writes: .factory/experiments/current_id -->
<!-- edges: unconditional → pre_eval -->
<!-- NOTE: command contains template values requiring CEO substitution -->

Open a new experiment for the current hypothesis. The CEO must substitute $HYPOTHESIS with the hypothesis text.

```bash
{{finalize_command_begin::factory begin $PROJECT_PATH --hypothesis "$HYPOTHESIS"}}
```

## Step: Pre Eval


<!-- node: FnNode id=pre_eval -->
<!-- command: python3 -c "import shutil; from pathlib import Path; src = Path('{project_path}/.factory/evolve/current_score.json'); exp_dir = Path('{project_path}/.factory/experiments/$EXP_ID'); exp_dir.mkdir(parents=True, exist_ok=True); shutil.copy2(str(src), str(exp_dir / 'eval_before.json')) if src.exists() else None; print('eval_before.json written to', exp_dir)" -->
<!-- reads: .factory/evolve/current_score.json -->
<!-- writes: .factory/experiments/$EXP_ID/eval_before.json -->
<!-- edges: unconditional → builder -->
<!-- NOTE: command contains template values requiring CEO substitution -->

Copy current score snapshot to experiment's eval_before.json. The CEO must substitute $EXP_ID with the experiment ID from begin. This enables CycleAnalyzer to compute per-experiment score deltas.

```bash
{{finalize_command_pre_eval::python3 -c "import shutil; from pathlib import Path; src = Path('$PROJECT_PATH/.factory/evolve/current_score.json'); exp_dir = Path('$PROJECT_PATH/.factory/experiments/$EXP_ID'); exp_dir.mkdir(parents=True, exist_ok=True); shutil.copy2(str(src), str(exp_dir / 'eval_before.json')) if src.exists() else None; print('eval_before.json written to', exp_dir)"}}
```

## Phase 3: Builder


<!-- node: AgentNode id=builder role=builder blocking=true -->
<!-- reads: .factory/evolve/current_best.py, .factory/strategy/current.md -->
<!-- writes: .factory/evolve/candidate.py, .factory/reviews/builder-latest.md -->
<!-- edges: unconditional → gate_build -->

```bash
factory agent builder --task "{{task_prompt_builder::Apply the code modification hypothesis to produce a candidate program. Read the hypothesis at .factory/strategy/current.md. Read the current best code at .factory/evolve/current_best.py. CRITICAL CONSTRAINTS:
- ONLY modify code between EVOLVE-BLOCK-START and EVOLVE-BLOCK-END markers
- Preserve ALL code outside evolution markers (imports, helpers, return format)
- Maintain function signatures and return types expected by the evaluator
- No external dependencies beyond what's in the initial program
- Validate Python syntax (AST parse check)
Write the complete modified program to .factory/experiments/$EXP_ID/candidate.py. Also copy it to .factory/evolve/candidate.py for the evaluator.
Read: .factory/evolve/current_best.py, .factory/strategy/current.md
Write output to: .factory/evolve/candidate.py, .factory/reviews/builder-latest.md}}" --project "$PROJECT_PATH" --timeout {{timeout_builder::1200}}
```

<!-- gate: GateNode id=gate_build evaluator_type=agent evaluator_role=ceo -->
<!-- reads: .factory/reviews/builder-latest.md -->
<!-- edges: proceed → health_checker, reloop → builder -->

### CEO Review — Build

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: {{gate_prompt_gate_build::Review builder output. Check:
1) candidate.py exists at .factory/evolve/candidate.py
2) Only EVOLVE-BLOCK regions were modified (diff the candidate against current_best.py)
3) Python syntax is valid
4) No external dependencies were added
REDIRECT to builder if constraints violated.}}
4. Write verdict to `.factory/reviews/ceo-verdict-build.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max {{max_iterations_gate_build::3}} iterations)*

## Phase 4: Health Checker


<!-- node: AgentNode id=health_checker role=health_checker blocking=true -->
<!-- reads: .factory/baseline/eval.json, .factory/evolve/candidate.py, .factory/evolve/current_score.json -->
<!-- writes: .factory/experiments/$EXP_ID/eval_after.json, .factory/reviews/health-check.md -->
<!-- edges: unconditional → post_eval -->

```bash
factory agent health_checker --task "{{task_prompt_health_checker::Evaluate the candidate program via MCP and compare scores. 1. Read the candidate code from .factory/evolve/candidate.py
2. Call evaluate_solution(candidate_code) via MCP tool
3. Parse the evaluate_solution() response fields (combined_score, validity, eval_time, and any domain-specific metrics)
4. Read current best score from .factory/evolve/current_score.json
5. Read baseline eval_time from .factory/baseline/eval.json
6. Apply verdict logic:
   - If validity == false: REVERT ('Invalid solution')
   - If combined_score <= current_score: REVERT ('Score degraded or unchanged')
   - If eval_time > 10 * baseline_eval_time: REVERT ('Unacceptable slowdown')
   - Otherwise: KEEP ('Score improved')
7. Write structured eval results as JSON to .factory/experiments/$EXP_ID/eval_after.json with these exact fields:
   {"combined_score": <float>, "validity": <bool>, "eval_time": <float>, "sum_radii": <float>, "target_ratio": <float>}
8. Write verdict with KEEP/REVERT and rationale to .factory/reviews/health-check.md
Include in the verdict: score_before, score_after, delta, validity, eval_time.
Read: .factory/baseline/eval.json, .factory/evolve/candidate.py, .factory/evolve/current_score.json
Write output to: .factory/experiments/$EXP_ID/eval_after.json, .factory/reviews/health-check.md}}" --project "$PROJECT_PATH" --timeout {{timeout_health_checker::600}}
```

## Step: Post Eval


<!-- node: FnNode id=post_eval -->
<!-- command: python3 -c "import json; from pathlib import Path; from datetime import datetime, timezone; score = None; ea = Path('{project_path}/.factory/experiments/$EXP_ID/eval_after.json'); if ea.exists():     d = json.loads(ea.read_text());     score = d.get('combined_score', d.get('total')); if score is None:     hc = Path('{project_path}/.factory/reviews/health-check.md');     if hc.exists():         for line in hc.read_text().splitlines():             if 'score_after' in line.lower() or 'combined_score' in line.lower():                 for part in line.split(':'):                     part = part.strip().rstrip(',%); ');                     try: score = float(part); break;                     except ValueError: pass;             if score is not None: break; event = {    'type': 'eval.completed',     'data': {'composite': score if score is not None else 0.0, 'exp_id': '$EXP_ID'},     'timestamp': datetime.now(timezone.utc).isoformat(), }; events_path = Path('{project_path}/.factory/events.jsonl'); with open(events_path, 'a') as f:     f.write(json.dumps(event) + chr(10)); print('eval.completed event emitted, composite=', score)" -->
<!-- reads: .factory/experiments/$EXP_ID/eval_after.json, .factory/reviews/health-check.md -->
<!-- writes: .factory/events.jsonl -->
<!-- edges: unconditional → gate_eval -->
<!-- NOTE: command contains template values requiring CEO substitution -->

Emit eval.completed event to events.jsonl after Health Checker finishes. The CEO must substitute $EXP_ID. Reads the composite score from eval_after.json (primary) or health-check.md (fallback), then appends a structured event for CycleAnalyzer._extract_scores().

```bash
{{finalize_command_post_eval::python3 -c "import json; from pathlib import Path; from datetime import datetime, timezone; score = None; ea = Path('$PROJECT_PATH/.factory/experiments/$EXP_ID/eval_after.json'); if ea.exists():     d = json.loads(ea.read_text());     score = d.get('combined_score', d.get('total')); if score is None:     hc = Path('$PROJECT_PATH/.factory/reviews/health-check.md');     if hc.exists():         for line in hc.read_text().splitlines():             if 'score_after' in line.lower() or 'combined_score' in line.lower():                 for part in line.split(':'):                     part = part.strip().rstrip(',%); ');                     try: score = float(part); break;                     except ValueError: pass;             if score is not None: break; event = {    'type': 'eval.completed',     'data': {'composite': score if score is not None else 0.0, 'exp_id': '$EXP_ID'},     'timestamp': datetime.now(timezone.utc).isoformat(), }; events_path = Path('$PROJECT_PATH/.factory/events.jsonl'); with open(events_path, 'a') as f:     f.write(json.dumps(event) + chr(10)); print('eval.completed event emitted, composite=', score)"}}
```

<!-- gate: GateNode id=gate_eval evaluator_type=agent evaluator_role=ceo -->
<!-- reads: .factory/reviews/health-check.md -->
<!-- edges: proceed → finalize -->

### CEO Review — Eval

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/health-check.md`
3. Assess: {{gate_prompt_gate_eval::Review the evaluation verdict at .factory/reviews/health-check.md.
Read the Health Checker's KEEP/REVERT recommendation and rationale.
If KEEP:
  - Update .factory/evolve/current_best.py with the candidate code
  - Update .factory/evolve/current_score.json with the new score
  - Set $VERDICT=keep for finalize
If REVERT:
  - Keep current_best.py unchanged
  - Set $VERDICT=revert for finalize
Then PROCEED to finalize and archival.}}
4. Write verdict to `.factory/reviews/ceo-verdict-eval.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Step: Finalize


<!-- node: FnNode id=finalize -->
<!-- command: factory finalize {project_path} --id $EXP_ID --verdict $VERDICT --hypothesis "$HYPOTHESIS" -->
<!-- reads: .factory/reviews/health-check.md -->
<!-- writes: .factory/experiments/verdict.json -->
<!-- edges: unconditional → archivist -->
<!-- NOTE: command contains template values requiring CEO substitution -->

Close the experiment with a keep/revert verdict. The CEO must substitute $EXP_ID, $VERDICT (keep/revert/error), and $HYPOTHESIS.

```bash
{{finalize_command_finalize::factory finalize $PROJECT_PATH --id $EXP_ID --verdict $VERDICT --hypothesis "$HYPOTHESIS"}}
```

## Phase 5: Archivist


<!-- node: AgentNode id=archivist role=archivist blocking=false -->
<!-- reads: .factory/experiments/verdict.json, .factory/reviews/health-check.md -->
<!-- writes: .factory/archive/experiment.md -->
<!-- edges: unconditional → gate_convergence -->

```bash
factory agent archivist --task "{{task_prompt_archivist::Archive evolve experiment results and learnings. Read the experiment verdict at .factory/experiments/verdict.json. Read the hypothesis at .factory/strategy/current.md. Read the eval results at .factory/reviews/health-check.md. If KEEP: document what worked (algorithm insight, parameter sweet spot). If REVERT: document why it failed (validity issue, wrong assumption, local optimum). Write learnings to .factory/archive/experiments/$EXP_ID.md.
Read: .factory/experiments/verdict.json, .factory/reviews/health-check.md
Write output to: .factory/archive/experiment.md}}" --project "$PROJECT_PATH" --timeout {{timeout_archivist::300}} --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

<!-- gate: GateNode id=gate_convergence evaluator_type=agent evaluator_role=ceo -->
<!-- reads: .factory/evolve/current_score.json -->
<!-- edges: reloop → strategist, proceed → archivist_final -->

### CEO Review — Convergence

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/evolve/current_score.json`
3. Assess: {{gate_prompt_gate_convergence::Check convergence criteria. Read .factory/evolve/current_score.json and .factory/results.tsv.
Exit (PROCEED) if ANY of:
  1. Target score reached (check factory.md convergence.target_score)
  2. Max cycles reached (check factory.md convergence.max_cycles, default 50)
  3. Diminishing returns: 5 consecutive cycles with improvement < 0.001
Continue (RELOOP to strategist) otherwise.
Log the convergence status: current_score, target, cycles_completed, recent_improvement_deltas.}}
4. Write verdict to `.factory/reviews/ceo-verdict-convergence.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `strategist` (max {{max_iterations_gate_convergence::3}} iterations)*

## Phase 6: Archivist Final


<!-- node: AgentNode id=archivist_final role=archivist blocking=true -->
<!-- reads: .factory/evolve/current_score.json -->
<!-- writes: .factory/archive/evolve-summary.md -->
<!-- edges: none -->

```bash
factory agent archivist --task "{{task_prompt_archivist_final::Final evolution summary. Write a comprehensive summary of the evolution run: total experiments, keep/revert counts, score trajectory (baseline to final), best-performing hypothesis categories, key learnings. Read .factory/results.tsv for full history. Write to .factory/archive/evolve-summary.md.
Read: .factory/evolve/current_score.json
Write output to: .factory/archive/evolve-summary.md}}" --project "$PROJECT_PATH" --timeout {{timeout_archivist_final::300}} --model haiku
```
