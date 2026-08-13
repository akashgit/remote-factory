# Lumen - RL Training for Einstein Arena

Reinforcement learning training system for mathematical optimization tasks from [Einstein Arena](https://github.com/einsteinarena/einsteinarena).

---

## Installation

⚠️ **Install this environment BEFORE using the workflow.**

### 1. Install Dependencies

```bash
cd factory/lumen
uv sync --no-install-project
```

This creates `.venv/` in `factory/lumen/` and installs all base dependencies from `pyproject.toml`.

### 2. Install PyTorch & vLLM

```bash
uv pip install vllm
```

This installs the latest vLLM (which brings PyTorch as a dependency) from PyPI with CUDA support.

**Note**: The installed versions are:
- PyTorch 2.13.0+cu130
- vLLM 0.27.1

### 3. Install VERL Fork

⚠️ **CRITICAL**: You MUST use the ash-ding fork, not official VERL.

```bash
git clone https://github.com/ash-ding/verl.git ~/verl
uv pip install -e ~/verl
```

**Why the fork?**
- Custom `entropic_adaptive_beta` advantage estimator (hardcoded in `run_verl.py`)
- DISCOVER environment variable forwarding
- Enhanced checkpoint/resume

Official VERL will fail with: `ConfigCompositionException: Could not find 'entropic_adaptive_beta'`

### 4. Verify

```bash
.venv/bin/python env_specs/verify_env.py
```

Expected output (CUDA check may fail if GPU drivers are outdated):
```
✓ torch                2.13.0+cu130
✓ vllm                 0.27.1
✓ verl                 0.9.0.dev
✓ numpy                2.3.5
...
```

**If CUDA check fails**: This is a driver issue, not an installation issue. The packages are correctly installed. Update NVIDIA drivers or use a machine with newer drivers for actual training.

---

## Usage

From the remote-factory root directory:

```bash
factory ceo /path/to/project --mode lumen
```

The workflow automatically uses `factory/lumen/.venv/bin/python`.

### Custom Python Path

Override the default:

```bash
export LUMEN_PYTHON=/custom/path/to/python
factory ceo /path/to/project --mode lumen
```

---

## Configuration

Create `.factory/lumen/config.json` in your project:

```json
{
  "task_name": "circle-packing",
  "task_dir": "benchmarks/einsteinarena/circle-packing",
  "model_path": "Qwen/Qwen3-8B",
  "num_gpus": 8,
  "rollout_tp": 4,
  "num_rollouts_per_prompt": 64,
  "mock": false
}
```

**Mock mode** (testing without GPUs):
```json
{"task_name": "circle-packing", "mock": true}
```

---

## Troubleshooting

### PyTorch can't find CUDA

Check NVIDIA drivers: `nvidia-smi`

If drivers are fine, verify PyTorch:
```bash
.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

### VERL not found

Install from ash-ding fork (see step 3 above).

### Training fails with "entropic_adaptive_beta not found"

You installed official VERL. Uninstall and reinstall from ash-ding fork:
```bash
cd /workspace/home/asherding/code/remote-factory/factory/lumen
uv pip uninstall verl
uv pip install -e ~/verl
```

---

## Files

- **pyproject.toml** - Project dependencies and uv configuration
- **preflight.py** - Environment validation
- **train.py** - Main training entry point
- **run_verl.py** - VERL training orchestration
- **verl_integration/** - Custom VERL extensions
- **env_specs/** - Verification and legacy requirements

---

## System Requirements

- NVIDIA GPU with CUDA 12.9+ drivers
- Python 3.11 (installed in .venv)
- ~5GB disk space
- 16GB+ RAM recommended

---

## Workflow Architecture

```
Preflight → Context Agent → RL Training → Check Gate
(Validate)  (Gen prompts)    (VERL+vLLM)  (Iterate/Finish)
```

1. **Preflight** - Validates environment, detects GPUs, resolves config
2. **Context Agent** - LLM generates 8 optimization prompts
3. **RL Training** - VERL PPO training with vLLM rollouts
4. **Check Gate** - Compares score to SOTA, decides to iterate or finish

---

## Development

```bash
# Run tests
pytest factory/workflow/contributed/lumen/test_workflow.py -v
pytest tests/test_lumen_*.py -v

# Update dependencies
# Edit pyproject.toml, then:
uv pip install -e . --refresh
```

---

## License

Same as remote-factory.
