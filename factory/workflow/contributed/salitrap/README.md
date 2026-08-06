# SaliTrap Benchmark Workflow

Commonsense reasoning under salience bias with numerical distractors.

[SaliTrap](https://github.com/Wuzheng02/SaliTrap) (arXiv 2607.28478) is a 1,145-task
benchmark measuring whether LLMs suppress known commonsense knowledge when distracted
by salient numerical details. It tests 4 trap dimensions: Missing Prerequisite,
Environmental Mismatch, Temporal/Physiological Violation, and Rule Mismatch.

## Pipeline

```
study ──► solver ──► gate_verify ──► auto_merge
             ▲            │
             └── RELOOP ──┘
```

- **study**: Catalog workspace and read task instruction from `/tmp/task-instruction.md`
- **solver**: Opus agent (3600s, 3 iterations) — physics-aware priming, identify trap, write structured answer
- **gate_verify**: fn evaluator — check `/workspace/answer.txt` exists with content and commits present
- **auto_merge**: Fast-forward main to the working branch

## Usage

```bash
factory workflow run salitrap .
```

## What Makes SaliTrap Different

| Aspect | SWE-bench | SaliTrap |
|--------|-----------|----------|
| Task type | Code modification | Commonsense reasoning |
| Input | Bug description + repo | Reasoning scenario with distractors |
| Agent behavior | Edit code, run tests | Identify traps, reason about feasibility |
| Output | Code patch | Structured textual answer |
| Evaluation | Test pass/fail | Trap Avoidance Rate (TAR) |

## Key Metrics

- **TAR** (Trap Avoidance Rate): Percentage of traps correctly identified
- **HFR** (Hard Fail Rate): Rate of complete reasoning failures
- **SCR** (Sycophantic Compliance Rate): Rate of blindly following scenario framing
- **SI** (Sycophancy Index): Composite measure of knowledge suppression

## MVP Approach

Single-pass evaluation with physics-aware priming (P1 intervention from the paper).
The solver prompt explicitly instructs the agent to verify physical prerequisites before
engaging with numerical calculations. This maps to the most effective intervention
(+31.4pp TAR for GLM-5.1 in the original study).
