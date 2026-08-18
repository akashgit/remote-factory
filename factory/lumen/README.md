# LUMEN: Learning-based Universal Modeling and Evolution eNgine

Reinforcement learning training system for scientific discovery tasks, starting with mathematical optimization from [Einstein Arena](https://github.com/einsteinarena/einsteinarena).

---

## Installation

**Install this environment BEFORE using the workflow.**

The installation mirrors [TTT-Discover](https://arxiv.org/abs/2601.16175)'s setup — same packages, same versions, same install order. This ensures VERL, vLLM, and the agent loop behave identically.

### Prerequisites

- 8x NVIDIA H100 80GB (or equivalent)
- CUDA Driver 12.9+
- Python 3.11
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)

### 1. Create Virtual Environment

```bash
cd factory/lumen
uv venv --python 3.11 .venv
source .venv/bin/activate
```

### 2. Install Base Dependencies

```bash
uv pip install --index-strategy unsafe-best-match -r requirements.txt
```

The `--index-strategy unsafe-best-match` flag is required because `requirements.txt` includes `--extra-index-url` for PyTorch cu129 wheels. Without it, uv may pick older packages (e.g., `packaging<=24.1`) from the PyTorch index instead of PyPI, breaking downstream installs.

### 3. Install vLLM (cu129 build)

**CRITICAL**: Install the cu129 build from GitHub releases with `--no-deps`, NOT from PyPI. The PyPI default targets CUDA 13 and will fail with `libcudart.so.13 not found`.

```bash
uv pip install --no-deps \
  "https://github.com/vllm-project/vllm/releases/download/v0.23.0/vllm-0.23.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl"
```

The `--no-deps` flag is essential — vllm's dependency resolver pulls in `nvidia-nccl-cu13` which overwrites the cu12 NCCL library and causes `CUDA driver version is insufficient` at training time.

### 4. Install FlashInfer

**CRITICAL**: Pin both `flashinfer-python` and `flashinfer-cubin` to the same version. A version mismatch (e.g., cubin 0.6.13 vs python 0.6.17) causes `RuntimeError: flashinfer-cubin version does not match flashinfer version` during vLLM worker init. Use `--no-deps` to prevent flashinfer from replacing torch.

```bash
uv pip install --no-deps "flashinfer-python==0.6.12" "flashinfer-cubin==0.6.12" \
  --extra-index-url https://flashinfer.ai/whl/cu129/torch2.11/
```

Verify PyTorch wasn't replaced:
```bash
python -c "import torch; print(torch.__version__)"  # must show 2.11.0+cu129
```
If it shows a different version, reinstall:
```bash
uv pip install --force-reinstall --no-deps \
  "https://download.pytorch.org/whl/cu129/torch-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl"
```

### 5. Install Flash-Attention

```bash
MAX_JOBS=8 uv pip install flash-attn --no-build-isolation --no-cache-dir
```

This compiles from source and takes 20-25 minutes.

### 6. Install VERL Fork

**CRITICAL**: Use the Discover fork (`ash-ding/verl`), not official VERL.

```bash
git clone https://github.com/ash-ding/verl.git /tmp/verl
uv pip install -e /tmp/verl
```

Or if you already have a local checkout (e.g., from Discover):
```bash
uv pip install -e /path/to/discover/verl
```

**Why the fork?**
- Custom `entropic_adaptive_beta` advantage estimator
- `_write_to_tq` format expected by the LUMEN agent loop
- Enhanced checkpoint/resume and rollout logging

Official VERL will fail with: `ConfigCompositionException: Could not find 'entropic_adaptive_beta'`

### 7. Fix NCCL (cu12 override)

VERL's dependencies may re-install `nvidia-nccl-cu13`, overwriting the cu12 NCCL library. Force reinstall cu12:

```bash
uv pip install --force-reinstall nvidia-nccl-cu12
```

Verify:
```bash
python -c "import torch; print(torch.cuda.nccl.version())"  # should show (2, 28, 9)
```

### 8. Verify

```bash
.venv/bin/python env_specs/verify_env.py
```

Expected output:
```
Core Packages:
✓ torch                2.11.0+cu129
✓ vllm                 0.23.0+cu129
✓ verl                 0.9.0.dev0
✓ numpy                2.3.x
...
```

---

## Usage

From the remote-factory root directory:

```bash
factory ceo /path/to/project --mode lumen
```

The workflow automatically uses `factory/lumen/.venv/bin/python`.

### Custom Python Path

Override the default (e.g., to use a conda environment):

```bash
export LUMEN_PYTHON=/path/to/python
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

### PyTorch installs CPU-only version

The `--extra-index-url` in `requirements.txt` should handle this. If it doesn't:
```bash
uv pip install --force-reinstall \
  "https://download.pytorch.org/whl/cu129/torch-2.11.0%2Bcu129-cp311-cp311-manylinux_2_28_x86_64.whl"
```

### "entropic_adaptive_beta not found"

You installed official VERL. Reinstall from the Discover fork:
```bash
uv pip uninstall verl
git clone https://github.com/ash-ding/verl.git /tmp/verl
uv pip install -e /tmp/verl
```

### FlashInfer version mismatch

If you see `RuntimeError: flashinfer-cubin version (X) does not match flashinfer version (Y)`, reinstall both at the same version with `--no-deps`:
```bash
uv pip uninstall flashinfer-python flashinfer-cubin
uv pip install --no-deps "flashinfer-python==0.6.12" "flashinfer-cubin==0.6.12" \
  --extra-index-url https://flashinfer.ai/whl/cu129/torch2.11/
```

### Training silently fails (exit code 1, no traceback)

Stale checkpoints from a previously failed run can cause VERL to silently exit. Delete the checkpoint directory and retry:
```bash
rm -rf <project>/checkpoints/lumen
```

---

## Workflow Architecture

```
Preflight -> Context Agent -> RL Training -> Check Gate
(Validate)   (Gen prompts)    (VERL+vLLM)   (Iterate/Finish)
```

1. **Preflight** - Validates environment, detects GPUs, resolves config
2. **Context Agent** - LLM generates 8 optimization prompts
3. **RL Training** - VERL PPO training with vLLM rollouts
4. **Check Gate** - Compares score to SOTA, decides to iterate or finish

---

## Files

- **requirements.txt** - Python dependencies (mirrors Discover's requirements-base.txt)
- **pyproject.toml** - Project metadata and uv configuration
- **preflight.py** - Environment validation
- **train.py** - Main training entry point
- **run_verl.py** - VERL training orchestration
- **verl_integration/** - Custom VERL extensions (agent loop, reward, data source)
- **env_specs/** - Environment verification scripts

---

## License

Same as remote-factory.
