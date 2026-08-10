# /optimize — Run Inner-Outer Optimization Loop

Run the factory's prompt optimization loop against a Harbor benchmark. This tunes the SearchQA skill prompt by executing benchmark tasks, evaluating accuracy, and using the AgenticMutator to iteratively improve the prompt.

## When to Use

- User asks to optimize benchmark scores, tune prompts, or improve SearchQA accuracy
- User wants to run the inner-outer optimization loop
- After making changes to the SearchQA skill and wanting to measure impact

## Procedure

1. Determine the project path (default: current working directory)
2. Run the optimization command via Bash:

```bash
factory optimize <project-path> \
  --benchmark searchqa \
  --steps 3 \
  --epochs 1 \
  --concurrency 10 \
  --model sonnet
```

3. Monitor the step-by-step output. Each step prints:
   - Accuracy for that step (correct/total)
   - Score delta from previous step
   - Verdict (keep/revert)

4. Report results to the user:
   - Baseline score (step 1 starting score)
   - Best score and which step achieved it
   - Final score after all steps
   - Total improvement delta

## Key Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--benchmark` | `searchqa` | Benchmark to run (`searchqa`, `featurebench`, or `auto`) |
| `--benchmark-dir` | none | Path to a custom benchmark directory (config.json + executor.py + evaluator.py) |
| `--steps` | `3` | Number of optimization steps per epoch |
| `--epochs` | `1` | Number of training epochs |
| `--concurrency` | `5` | Number of concurrent Harbor tasks |
| `--git-ref` | current branch | Git ref the Harbor agent checks out |
| `--docker-host` | from `DOCKER_HOST` env | Docker/Podman socket path |
| `--model` | `sonnet` | Model for the AgenticMutator |
| `--skill-path` | auto | Path to initial skill file to optimize |

## Dynamic Benchmarks

Use `--benchmark auto` to load a benchmark from the project's `.factory/eval/benchmark/` directory, or `--benchmark-dir /path/to/dir` to load from any directory. The directory must contain:

- `config.json` — benchmark metadata (must have `name`; optional `executor_params` and `evaluator_params` dicts)
- `executor.py` — module with an `Executor` class that has an `execute` method
- `evaluator.py` — module with an `Evaluator` class that has `parse`, `parse_many`, and `get_info` methods

```bash
# Auto-detect from project's .factory/eval/benchmark/
factory optimize /path/to/project --benchmark auto --steps 5

# Explicit directory
factory optimize /path/to/project --benchmark-dir /path/to/my-benchmark --steps 3
```

## Follow-Up Guidance

- If no improvement after 3 steps: try increasing to `--steps 5` or `--steps 10`
- If runs are slow: reduce `--concurrency` to avoid resource contention
- If accuracy is already high (>0.85): the mutator may plateau — try a different model
- To use a custom starting skill: pass `--skill-path path/to/skill.md`
