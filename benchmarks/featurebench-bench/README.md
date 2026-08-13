# FeatureBench Direct-Workflow Benchmark

Benchmarks the factory's `featurebench` workflow against the standard FeatureBench baseline agent (`claude_code`).

## Prerequisites

- Docker running (for extracting task repos from FeatureBench images)
- `factory` CLI installed and on PATH
- `fb` CLI installed (for baseline runs and evaluation)
- `datasets` Python package (`pip install datasets`)

## Usage

### Run specific tasks (factory workflow only)

```bash
python bench.py --task-id "astropy__astropy.b0db0daa.test_tilde_path.383d7c0c.lv1" --factory-only
```

### Run a split file

```bash
python bench.py --split ../featurebench-splits/val.jsonl --factory-only --timeout 1800
```

### Run both factory and baseline, then compare

```bash
python bench.py --task-id "astropy__astropy.b0db0daa.test_tilde_path.383d7c0c.lv1" --timeout 1800
```

### Baseline only

```bash
python bench.py --task-id "astropy__astropy.b0db0daa.test_tilde_path.383d7c0c.lv1" \
    --baseline-only --model claude-sonnet-4-20250514
```

### Custom results directory

```bash
python bench.py --split val.jsonl --factory-only --results-dir /tmp/fb-results
```

### Skip evaluation (just produce patches)

```bash
python bench.py --task-id "task1" "task2" --factory-only --skip-eval
```

## Output

Results are written to `--results-dir` (default: `benchmarks/featurebench-bench/results/`):

```
results/
├── factory/
│   └── output.jsonl          # One JSON entry per task
├── baseline/
│   └── output.jsonl          # Baseline agent output
└── comparison_report.json    # Side-by-side comparison
```

Each `output.jsonl` entry:
```json
{"instance_id": "...", "model_patch": "<diff>", "agent": "factory_workflow", "model": "...", "success": true}
```
