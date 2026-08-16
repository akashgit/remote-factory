# LUMEN: Learning-based Universal Modeling and Evolution eNgine

Reinforcement learning training system for scientific discovery tasks, starting with mathematical optimization from [Einstein Arena](https://github.com/einsteinarena/einsteinarena).

---

## Installation

⚠️ **Install this environment BEFORE using the workflow.**

### 1. Install Base Dependencies

```bash
cd factory/lumen
uv sync --no-install-project
```

This creates `.venv/` in `factory/lumen/` and installs all base dependencies from `pyproject.toml`.

### 2. Install PyTorch & vLLM (CUDA 12.9)

⚠️ **Version Alignment with Discover**: We use the same versions as TTT-Discover for compatibility.

```bash
# Install PyTorch 2.11.0+cu129 from direct wheel URLs
uv pip install \
  "https://download.pytorch.org/whl/cu129/torch-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl" \
  "https://download.pytorch.org/whl/cu129/torchvision-0.26.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl" \
  "https://download.pytorch.org/whl/cu129/torchaudio-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl"

# Install vLLM 0.23.0+cu129 (--no-deps to avoid CUDA version conflicts)
uv pip install --no-deps https://github.com/vllm-project/vllm/releases/download/v0.23.0/vllm-0.23.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl
```

**Why this way?**
- PyTorch wheels: Must use direct URLs because `--extra-index-url` doesn't prioritize `+cu129` suffix
- vLLM `--no-deps`: vLLM's dependencies don't specify CUDA version for PyTorch, would install wrong version
- vLLM dependencies: Already included in `pyproject.toml` (installed in Step 1)

### 3. Install FlashInfer & Flash-Attention

```bash
# Install matching flashinfer versions (0.6.12)
uv pip install flashinfer-python==0.6.12 flashinfer-cubin==0.6.12

# Restore PyTorch cu129 (flashinfer may downgrade it)
uv pip install --force-reinstall --no-deps \
  "https://download.pytorch.org/whl/cu129/torch-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl" \
  "https://download.pytorch.org/whl/cu129/torchvision-0.26.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl" \
  "https://download.pytorch.org/whl/cu129/torchaudio-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl"

# Restore nvidia-nccl-cu12
uv pip install --force-reinstall nvidia-nccl-cu12==2.28.9

# Compile flash-attn from source
MAX_JOBS=8 uv pip install flash-attn --no-build-isolation --no-cache-dir
```

**Note**: Flash-attention compilation can take 20-25 minutes.

### 4. Install VERL Fork

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

### 5. Verify

```bash
.venv/bin/python env_specs/verify_env.py
```

Expected output (CUDA check may fail if GPU drivers are outdated):
```
✓ torch                2.6.0+cu129
✓ vllm                 0.23.0
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
  "max_iterations": 3,
  "mock": false
}
```

**Configuration fields:**
- `task_name`: Einstein Arena task name (required)
- `task_dir`: Path to task directory (optional, defaults to `benchmarks/einsteinarena/{task_name}`)
- `model_path`: HuggingFace model path (required)
- `num_gpus`: Number of GPUs to use (auto-detected if not specified)
- `rollout_tp`: Tensor parallelism for vLLM rollouts (default: 4)
- `num_rollouts_per_prompt`: Number of rollouts per prompt (default: 64)
- `max_iterations`: Maximum number of training iterations (default: 3)
- `mock`: Enable mock mode for testing without GPUs (default: false)

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

- NVIDIA GPU with CUDA 12.9 toolkit + drivers (toolkit required for flash-attn compilation, drivers for PyTorch cu129 runtime)
- Python 3.11 (installed in .venv)
- ~8GB disk space (flash-attn compilation requires extra space)
- 16GB+ RAM recommended
- 8+ GPUs recommended for distributed training

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
