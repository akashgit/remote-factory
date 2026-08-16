# Outer Loop Workflow

Evolutionary search for optimal workflow DAGs — evolves factory modes against benchmarks using population-based optimization.

## Graph

```
seed (FnNode) → evaluate (FnNode) → reflect (FnNode) → evolve (FnNode) → gate_converge (GateNode)
                    ↑                                                            │
                    └──────────────── RELOOP (until convergence) ────────────────┘
```

- **seed**: Initializes the population from a base workflow via `factory outer-loop calibrate`
- **evaluate**: Evaluates current generation's candidates against benchmark instances
- **reflect**: Runs contrastive reflection on winner/loser CycleRecord exhaust
- **evolve**: Produces offspring via reflection-guided mutations
- **gate_converge**: Checks convergence criteria (plateau, diversity collapse, budget, target score)

## Usage

```bash
factory workflow run outer-loop --project /path/to/project
```

Typically orchestrated by the CEO in outer-loop mode. Each generation evaluates a population of candidate workflows, reflects on performance patterns, and produces informed mutations for the next generation.
