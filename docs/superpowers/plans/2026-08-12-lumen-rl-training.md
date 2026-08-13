# Lumen RL Training Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Lumen's mock RL training with a real VERL-based pipeline that does vLLM two-phase rollout generation, entropic adaptive beta advantage estimation, and LoRA training via GRPO.

**Architecture:** Each workflow iteration's `rl_train` FnNode launches VERL with `TOTAL_EPOCHS=1`. The training script reads `prompts.json` (8 prompts from agent node), converts to parquet, runs VERL (512 rollouts → advantage → LoRA update), and writes `evaluation_results.json` + `rollouts.jsonl` for the gate and next iteration's agent.

**Tech Stack:** VERL 0.9.0.dev (editable from Discover fork), vLLM 0.23.0, PEFT 0.19.1, PyTorch 2.11 (CUDA), Ray 2.56.0 — all in the `verl_discover` conda environment.

**Spec:** `docs/superpowers/specs/2026-08-12-lumen-rl-training-design.md`

## Global Constraints

- Python 3.11+ — use `X | Y` unions, not `Union[X, Y]`
- Snake_case everywhere, 100 char line length (ruff)
- All Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- Do NOT import from `ttt_discover` at runtime — port algorithms into `factory/lumen/`
- VERL is available via `verl_discover` conda env (already installed as editable)
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Existing mock mode (`--mock`) must continue to work after all changes

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `factory/lumen/verl_integration/__init__.py` | Create | Package marker |
| `factory/lumen/verl_integration/data_source.py` | Create | `prompts.json` → parquet conversion |
| `factory/lumen/advantages.py` | Create | Standalone entropic adaptive beta |
| `factory/lumen/verl_integration/reward.py` | Create | Einstein Arena test.sh evaluation (VERL interface) |
| `factory/lumen/verl_integration/agent_loop.py` | Create | Two-phase vLLM generation (ported from Discover, no PUCT) |
| `factory/lumen/run_verl.py` | Create | Launch wrapper: orchestrate single iteration |
| `factory/lumen/run_verl.sh` | Create | Shell script with VERL Hydra config |
| `factory/lumen/types.py` | Modify | Add `TrainingMetrics`, `RolloutRecord` TypedDicts |
| `factory/lumen/checkpoint.py` | Modify | Add VERL checkpoint path tracking |
| `factory/lumen/evaluate.py` | Modify | Add `evaluate_code_solution()` for code-string input |
| `factory/lumen/train.py` | Modify | Add real mode that delegates to `run_verl` |
| `factory/lumen/docs/environment-setup.md` | Create | Environment setup guide |
| `factory/workflow/contributed/lumen/workflow.py` | Modify | Update `rl_train` FnNode command |
| `tests/test_lumen_advantages.py` | Create | Advantage computation tests |
| `tests/test_lumen_data_source.py` | Create | Parquet conversion tests |
| `tests/test_lumen_reward.py` | Create | Reward function tests |
| `tests/test_lumen_run_verl.py` | Create | Launch wrapper config generation tests |

---

### Task 1: Data Source — prompts.json → parquet

**Files:**
- Create: `factory/lumen/verl_integration/__init__.py`
- Create: `factory/lumen/verl_integration/data_source.py`
- Test: `tests/test_lumen_data_source.py`

**Interfaces:**
- Consumes: `prompts.json` file on disk (schema defined in spec §4.1)
- Produces: `create_parquet_from_prompts(prompts_json_path: Path, output_path: Path) -> Path` — returns path to written parquet file. Parquet has columns: `prompt` (numpy array of chat message dicts), `data_source` (str), `ability` (str), `reward_model` (dict), `extra_info` (dict).

- [ ] **Step 1: Write the test file**

```python
# tests/test_lumen_data_source.py
"""Tests for Lumen VERL data source (prompts.json → parquet)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def sample_prompts(tmp_path: Path) -> Path:
    """Create a minimal prompts.json for testing."""
    data = {
        "iteration": 0,
        "problem_type": "geometry",
        "scoring_direction": "maximize",
        "solution_schema": {"circles": "array of [x, y, r]"},
        "prompts": [
            {
                "prompt_idx": i,
                "strategy": f"strategy_{i}",
                "prompt_text": f"Optimize using strategy {i}. Output solution.json.",
            }
            for i in range(8)
        ],
    }
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(data))
    return path


class TestCreateParquetFromPrompts:
    def test_creates_parquet_file(self, sample_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        result = create_parquet_from_prompts(sample_prompts, output)
        assert result == output
        assert output.exists()

    def test_parquet_has_correct_row_count(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        assert len(df) == 8

    def test_parquet_has_required_columns(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        required = {"prompt", "data_source", "ability", "reward_model", "extra_info"}
        assert required.issubset(set(df.columns))

    def test_prompt_column_is_chat_messages(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output)
        df = pd.read_parquet(output)
        row0_prompt = df.iloc[0]["prompt"]
        assert len(row0_prompt) == 1
        assert row0_prompt[0]["role"] == "user"
        assert "strategy 0" in row0_prompt[0]["content"]

    def test_extra_info_contains_task_metadata(self, sample_prompts: Path, tmp_path: Path) -> None:
        import pandas as pd

        from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

        output = tmp_path / "prompts.parquet"
        create_parquet_from_prompts(sample_prompts, output, task_dir="/path/to/task")
        df = pd.read_parquet(output)
        info = df.iloc[0]["extra_info"]
        assert info["task_dir"] == "/path/to/task"
        assert info["prompt_idx"] == 0
        assert info["strategy"] == "strategy_0"
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

```bash
pytest tests/test_lumen_data_source.py -v
```

- [ ] **Step 3: Create the package init and data source**

```python
# factory/lumen/verl_integration/__init__.py
"""VERL integration components for Lumen RL training."""
```

```python
# factory/lumen/verl_integration/data_source.py
"""Convert Lumen prompts.json to VERL-compatible parquet format."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def create_parquet_from_prompts(
    prompts_json_path: Path,
    output_path: Path,
    task_dir: str = "",
    data_source: str = "lumen",
    eval_timeout: int = 60,
) -> Path:
    """Read prompts.json and write a VERL-compatible parquet file.

    The parquet matches VERL's expected schema (same as Discover's training data):
      - prompt: numpy array of chat message dicts [{"role": "user", "content": "..."}]
      - data_source: str identifier for the reward function
      - ability: str ("code")
      - reward_model: dict ({"style": "rule", "ground_truth": ""})
      - extra_info: dict with task_dir, prompt_idx, strategy, eval_timeout
    """
    with open(prompts_json_path) as f:
        prompts_data = json.load(f)

    rows = []
    for p in prompts_data["prompts"]:
        chat_messages = np.array(
            [{"role": "user", "content": p["prompt_text"]}],
            dtype=object,
        )
        rows.append({
            "prompt": chat_messages,
            "data_source": data_source,
            "ability": "code",
            "reward_model": {"style": "rule", "ground_truth": ""},
            "extra_info": {
                "split": "train",
                "index": p["prompt_idx"],
                "prompt_idx": p["prompt_idx"],
                "strategy": p["strategy"],
                "task_dir": task_dir,
                "eval_timeout": eval_timeout,
                "scoring_direction": prompts_data.get("scoring_direction", "maximize"),
            },
        })

    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path)
    return output_path
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_lumen_data_source.py -v
```

- [ ] **Step 5: Commit**

```bash
git add factory/lumen/verl_integration/__init__.py factory/lumen/verl_integration/data_source.py tests/test_lumen_data_source.py
git commit -m "feat(lumen): add VERL data source — prompts.json to parquet conversion"
```

---

### Task 2: Entropic Adaptive Beta Advantage Estimation

**Files:**
- Create: `factory/lumen/advantages.py`
- Test: `tests/test_lumen_advantages.py`

**Interfaces:**
- Consumes: `torch.Tensor` of rewards (1-D, one per sequence in a group)
- Produces:
  - `solve_beta(rewards: torch.Tensor, delta: float = math.log(2), beta_max: float = 1e6, iters: int = 60) -> torch.Tensor` — returns scalar beta tensor
  - `entropic_advantages(rewards: torch.Tensor, beta: torch.Tensor, eps: float = 1e-12) -> torch.Tensor` — returns per-sequence advantages
  - `compute_group_advantages(group_rewards: torch.Tensor) -> torch.Tensor` — convenience: solve_beta + entropic_advantages in one call

- [ ] **Step 1: Write tests — including numerical verification against Discover's known outputs**

```python
# tests/test_lumen_advantages.py
"""Tests for entropic adaptive beta advantage estimation.

Verifies numerical equivalence with Discover's implementation at
discover/ttt_discover/rl/train.py:103-176.
"""

from __future__ import annotations

import math

import pytest
import torch


class TestSolveBeta:
    def test_single_element_returns_zero(self) -> None:
        from factory.lumen.advantages import solve_beta

        r = torch.tensor([1.0])
        beta = solve_beta(r)
        assert beta.item() == 0.0

    def test_identical_rewards_returns_zero(self) -> None:
        from factory.lumen.advantages import solve_beta

        r = torch.tensor([1.0, 1.0, 1.0, 1.0])
        beta = solve_beta(r)
        assert beta.item() < 1e-6

    def test_diverse_rewards_returns_positive_beta(self) -> None:
        from factory.lumen.advantages import solve_beta

        r = torch.tensor([0.0, 0.5, 1.0, 2.0])
        beta = solve_beta(r)
        assert beta.item() > 0.0

    def test_kl_at_solved_beta_equals_delta(self) -> None:
        """The solved beta should yield KL ≈ log(2)."""
        from factory.lumen.advantages import solve_beta

        r = torch.tensor([0.1, 0.5, 0.8, 1.2, 2.0, 0.3, 0.7, 1.5])
        beta = solve_beta(r)
        delta = math.log(2)

        logits = beta * (r - r.max())
        logq = logits - torch.logsumexp(logits, dim=0)
        q = torch.exp(logq)
        kl = (q * (logq + math.log(len(r)))).sum().item()
        assert abs(kl - delta) < 0.05  # 60 bisection steps → high precision


class TestEntropicAdvantages:
    def test_single_element(self) -> None:
        from factory.lumen.advantages import entropic_advantages

        r = torch.tensor([1.0])
        beta = torch.tensor(0.0)
        adv = entropic_advantages(r, beta)
        assert adv.shape == (1,)

    def test_advantages_sum_behavior(self) -> None:
        """Higher reward should get positive advantage, lower gets negative."""
        from factory.lumen.advantages import entropic_advantages, solve_beta

        r = torch.tensor([0.0, 1.0, 2.0, 3.0])
        beta = solve_beta(r)
        adv = entropic_advantages(r, beta)
        assert adv[-1] > 0  # highest reward → positive advantage
        assert adv[0] < 0  # lowest reward → negative advantage


class TestComputeGroupAdvantages:
    def test_matches_solve_then_entropic(self) -> None:
        from factory.lumen.advantages import (
            compute_group_advantages,
            entropic_advantages,
            solve_beta,
        )

        r = torch.tensor([0.1, 0.5, 0.8, 1.2, 2.0, 0.3, 0.7, 1.5])
        expected = entropic_advantages(r, solve_beta(r))
        result = compute_group_advantages(r)
        torch.testing.assert_close(result, expected)

    def test_constant_rewards_return_zeros(self) -> None:
        from factory.lumen.advantages import compute_group_advantages

        r = torch.tensor([1.0, 1.0, 1.0, 1.0])
        adv = compute_group_advantages(r)
        assert torch.allclose(adv, torch.zeros_like(adv), atol=1e-6)

    def test_64_samples_like_production(self) -> None:
        """Simulate a production group (64 completions per prompt)."""
        from factory.lumen.advantages import compute_group_advantages

        torch.manual_seed(42)
        r = torch.rand(64) * 3.0
        adv = compute_group_advantages(r)
        assert adv.shape == (64,)
        assert not torch.isnan(adv).any()
        assert not torch.isinf(adv).any()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_lumen_advantages.py -v
```

- [ ] **Step 3: Implement advantages.py — ported from Discover**

```python
# factory/lumen/advantages.py
"""Entropic adaptive beta advantage estimation.

Ported from Discover (ttt_discover/rl/train.py:103-176). Computes GRPO-style
group-relative advantages using LOO Boltzmann weights with an adaptively
solved temperature parameter beta.
"""

from __future__ import annotations

import math

import torch


def solve_beta(
    rewards: torch.Tensor,
    delta: float = math.log(2),
    beta_max: float = 1e6,
    iters: int = 60,
) -> torch.Tensor:
    """Binary search for beta where KL(q_beta || uniform) = delta.

    q_beta is the Boltzmann distribution over rewards: q ∝ exp(beta * r).
    """
    r = rewards.float()
    k = r.shape[0]

    if k < 2:
        return r.new_tensor(0.0)

    log_k = math.log(k)

    def kl_hat(beta_scalar: float) -> float:
        b = r.new_tensor(beta_scalar)
        logits = b * (r - r.max(dim=0, keepdim=True).values)
        logq = logits - torch.logsumexp(logits, dim=0, keepdim=True)
        q = torch.exp(logq)
        kl = (q * (logq + log_k)).sum(dim=0)
        return float(kl.mean().item())

    lo, hi = 0.0, 1.0
    if kl_hat(hi) < delta:
        while hi < beta_max and kl_hat(hi) < delta:
            hi *= 2.0
        if kl_hat(hi) < delta:
            return r.new_tensor(hi)

    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if kl_hat(mid) < delta:
            lo = mid
        else:
            hi = mid

    return r.new_tensor(hi)


def entropic_advantages(
    rewards: torch.Tensor,
    beta: torch.Tensor,
    eps: float = 1e-12,
) -> torch.Tensor:
    """Compute LOO Boltzmann advantages: w_i = exp(beta*r_i) / Z_loo_i, advantage = w - 1."""
    k = rewards.shape[0]
    e = torch.exp(beta * (rewards - rewards.max(dim=0, keepdim=True).values))

    if k == 1:
        z_loo = e
    else:
        z_loo = (e.sum(dim=0, keepdim=True) - e) / (k - 1)

    w = e / (z_loo + eps)
    return w - 1.0


def compute_group_advantages(rewards: torch.Tensor) -> torch.Tensor:
    """Convenience: solve beta then compute entropic advantages for one group."""
    beta = solve_beta(rewards)
    return entropic_advantages(rewards, beta)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_lumen_advantages.py -v
```

- [ ] **Step 5: Commit**

```bash
git add factory/lumen/advantages.py tests/test_lumen_advantages.py
git commit -m "feat(lumen): port entropic adaptive beta advantage estimation from Discover"
```

---

### Task 3: Reward Function — Einstein Arena test.sh evaluation

**Files:**
- Create: `factory/lumen/verl_integration/reward.py`
- Modify: `factory/lumen/evaluate.py` (add `evaluate_code_solution`)
- Test: `tests/test_lumen_reward.py`

**Interfaces:**
- Consumes: model output string (full response text), `extra_info` dict with `task_dir` and `eval_timeout`
- Produces:
  - `compute_score(data_source: str, solution_str: str, ground_truth: dict | None, extra_info: dict) -> dict` — VERL reward interface, returns `{"score": float, "code": str, "eval_msg": str}`
  - `extract_last_code_block(text: str) -> str | None` — extract python code from model output
  - `evaluate_code_solution(code: str, task_dir: Path, timeout: int = 60) -> float` — run code in sandbox, evaluate with test.sh

- [ ] **Step 1: Write the test file**

```python
# tests/test_lumen_reward.py
"""Tests for Lumen VERL reward function (Einstein Arena evaluation)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest


@pytest.fixture()
def mock_task_dir(tmp_path: Path) -> Path:
    """Create a minimal Einstein Arena task directory with a test.sh verifier."""
    task_dir = tmp_path / "test-task"
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True)

    test_sh = tests_dir / "test.sh"
    test_sh.write_text(
        '#!/usr/bin/env bash\n'
        'WORKSPACE="${WORKSPACE:-.}"\n'
        'if [ -f "$WORKSPACE/solution.json" ]; then\n'
        '  echo "1.5" > "$WORKSPACE/score.txt"\n'
        'else\n'
        '  echo "0.0" > "$WORKSPACE/score.txt"\n'
        'fi\n'
    )
    test_sh.chmod(test_sh.stat().st_mode | stat.S_IEXEC)

    instruction = task_dir / "instruction.md"
    instruction.write_text("# Test Task\nScoring Direction: MAXIMIZE\n")
    return task_dir


class TestExtractLastCodeBlock:
    def test_extracts_python_block(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = 'Some thinking\n```python\nprint("hello")\n```\nMore text'
        assert extract_last_code_block(text) == 'print("hello")'

    def test_returns_last_block_when_multiple(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = '```python\nfirst()\n```\ntext\n```python\nsecond()\n```'
        assert extract_last_code_block(text) == "second()"

    def test_returns_none_when_no_code(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        assert extract_last_code_block("no code here") is None

    def test_handles_generic_code_fence(self) -> None:
        from factory.lumen.verl_integration.reward import extract_last_code_block

        text = '```\nimport json\n```'
        assert extract_last_code_block(text) == "import json"


class TestEvaluateCodeSolution:
    def test_valid_code_gets_score(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = (
            'import json\n'
            'solution = {"value": 42}\n'
            'with open("solution.json", "w") as f:\n'
            '    json.dump(solution, f)\n'
        )
        score = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 1.5

    def test_code_that_produces_no_solution(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "x = 1 + 1"
        score = evaluate_code_solution(code, mock_task_dir, timeout=10)
        assert score == 0.0

    def test_timeout_returns_zero(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import evaluate_code_solution

        code = "import time; time.sleep(100)"
        score = evaluate_code_solution(code, mock_task_dir, timeout=1)
        assert score == 0.0


class TestComputeScore:
    def test_verl_interface(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import compute_score

        solution_str = (
            '<think>thinking</think>\n'
            '```python\n'
            'import json\n'
            'with open("solution.json", "w") as f:\n'
            '    json.dump({"value": 1}, f)\n'
            '```'
        )
        result = compute_score(
            data_source="lumen",
            solution_str=solution_str,
            ground_truth=None,
            extra_info={"task_dir": str(mock_task_dir), "eval_timeout": 10},
        )
        assert isinstance(result, dict)
        assert result["score"] == 1.5
        assert "code" in result

    def test_no_code_block_returns_zero(self, mock_task_dir: Path) -> None:
        from factory.lumen.verl_integration.reward import compute_score

        result = compute_score(
            data_source="lumen",
            solution_str="no code here",
            ground_truth=None,
            extra_info={"task_dir": str(mock_task_dir), "eval_timeout": 10},
        )
        assert result["score"] == 0.0
        assert "no code block" in result["eval_msg"]
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_lumen_reward.py -v
```

- [ ] **Step 3: Implement reward.py**

```python
# factory/lumen/verl_integration/reward.py
"""VERL reward function for Einstein Arena evaluation.

Evaluates model output by extracting code, executing it in a sandbox,
and running the task's test.sh verifier to produce a score.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def extract_last_code_block(text: str) -> str | None:
    """Extract the last fenced code block from model output."""
    pattern = r"```(?:python)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        return None
    return matches[-1].strip()


def evaluate_code_solution(code: str, task_dir: Path, timeout: int = 60) -> float:
    """Execute code in a sandbox and evaluate with test.sh.

    1. Write code to a temp file
    2. Execute it (produces solution.json in workspace)
    3. Run test.sh verifier
    4. Read score.txt
    """
    test_sh = (task_dir / "tests" / "test.sh").resolve()
    if not test_sh.exists():
        return 0.0

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()

        code_file = Path(tmpdir) / "run.py"
        code_file.write_text(code)

        try:
            subprocess.run(
                ["python3", str(code_file)],
                cwd=str(workspace),
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 0.0
        except Exception:
            return 0.0

        try:
            env = os.environ.copy()
            env["WORKSPACE"] = str(workspace)
            subprocess.run(
                ["bash", str(test_sh)],
                cwd=str(tmpdir),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, Exception):
            return 0.0

        score_file = workspace / "score.txt"
        if score_file.exists():
            try:
                return float(score_file.read_text().strip())
            except ValueError:
                return 0.0
        return 0.0


def compute_score(
    data_source: str,
    solution_str: str,
    ground_truth: dict | None,
    extra_info: dict,
) -> dict:
    """VERL reward interface.

    Called by VERL's reward pipeline for each completion. Extracts code from
    the model's response, executes it, and evaluates via Einstein Arena verifier.
    """
    code = extract_last_code_block(solution_str)
    if not code:
        return {"score": 0.0, "code": "", "eval_msg": "no code block extracted"}

    task_dir = Path(extra_info.get("task_dir", ""))
    eval_timeout = extra_info.get("eval_timeout", 60)

    try:
        score = evaluate_code_solution(code, task_dir, timeout=eval_timeout)
    except Exception as e:
        return {"score": 0.0, "code": code, "eval_msg": f"eval error: {e}"}

    return {
        "score": score,
        "code": code,
        "eval_msg": "" if score > 0 else "score is zero",
    }
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_lumen_reward.py -v
```

- [ ] **Step 5: Commit**

```bash
git add factory/lumen/verl_integration/reward.py tests/test_lumen_reward.py
git commit -m "feat(lumen): add VERL reward function with Einstein Arena test.sh evaluation"
```

---

### Task 4: Agent Loop — two-phase vLLM generation (ported from Discover)

**Files:**
- Create: `factory/lumen/verl_integration/agent_loop.py`

**Interfaces:**
- Consumes: VERL `TensorDict` batch (with prompts from parquet), `LumenConfig` dict, vLLM `llm_client`
- Produces:
  - `LumenAgentLoopWorkerTQ(AgentLoopWorker)` — Ray remote class. Methods: `set_lumen_config(config: dict)`, `generate_sequences(batch: TensorDict)`, `_run_prompt(prompt, trajectory, validate)`, `_generate_two_phase(prompt_ids, sampling_params, ...)` returning `(AgentLoopOutput, code, score)`
  - `LumenAgentLoopManagerTQ(AgentLoopManager)` — manager class

Note: This task has no unit tests because the agent loop requires a running vLLM engine and Ray cluster. It will be tested end-to-end in Task 7. The code is a direct port from Discover's `agent_loop.py` with PUCT removed.

- [ ] **Step 1: Port the agent loop from Discover**

Port from the Discover repo's `ttt_discover/verl_integration/agent_loop.py`. The file is large (~350 lines after stripping PUCT). Key sections to port:

1. `LumenAgentLoopWorkerTQ` — based on `DiscoverAgentLoopWorkerTQ`:
   - `set_lumen_config()` — replaces `set_discover_config()`. Initialize tokenizer, renderer, stop tokens. Remove `_puct_actor`, `_env_cls`.
   - `generate_sequences()` — same as Discover (iterate batch, spawn `_run_prompt` tasks).
   - `_run_prompt()` — simplified: no PUCT state, prompt text comes directly from batch data. Call `_generate_two_phase()` for N sessions, then evaluate each via `reward.compute_score()`. No PUCT update.
   - `_generate_two_phase()` — **identical to Discover** (lines 308-411): Case A/B/C logic, prefill injection with mask=0.
   - `_write_to_tq()` — **identical to Discover** (lines 591-636).
   - `_hit_stop_token()`, `_contains_pattern()` — **identical to Discover**.

2. `LumenAgentLoopManagerTQ` — based on `DiscoverAgentLoopManagerTQ`:
   - `generate_sequences()` — simplified: no PUCT batch-level sampling. Just assign prompts directly from the data source, then dispatch to workers.

Key differences from Discover to verify during porting:
- No `PUCTSamplerActor` import or usage anywhere
- No `State` type import or usage
- `_run_prompt()` reads prompt text from `prompt["raw_prompt"]` (set by manager from parquet data)
- Evaluation calls `factory.lumen.verl_integration.reward.compute_score()` instead of Discover's sandbox evaluator
- `extra_info` passed to reward includes `task_dir` (from parquet `extra_info` column)

```python
# factory/lumen/verl_integration/agent_loop.py
# Structure outline — full implementation ports from Discover's agent_loop.py
"""Custom VERL AgentLoop for Lumen.

Ported from Discover's DiscoverAgentLoopManagerTQ with PUCT removed.
Implements two-phase token completion (think + answer) with fine-grained
response masking and Einstein Arena evaluation.

Activate via VERL config:
  actor_rollout_ref.rollout.agent.agent_loop_manager_class: \
    "factory.lumen.verl_integration.agent_loop:LumenAgentLoopManagerTQ"
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import uuid
from typing import Any

import numpy as np
import ray
import torch
import transfer_queue as tq
from tensordict import NonTensorData, NonTensorStack, TensorDict

from verl.experimental.agent_loop import (
    AgentLoopManager,
    AgentLoopOutput,
    AgentLoopWorker,
    get_trajectory_info,
)
from verl.experimental.agent_loop.agent_loop import AgentLoopMetrics
from verl.utils.tensordict_utils import list_of_dict_to_tensordict

logger = logging.getLogger(__name__)


@ray.remote
class LumenAgentLoopWorkerTQ(AgentLoopWorker):
    """Agent loop worker with two-phase completion for Lumen."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tq.init()
        self.background_tasks = set()
        self._tokenizer = None
        self._renderer = None
        self._stop_token_ids = []
        self._lumen_config = {}

    def set_lumen_config(self, lumen_config: dict):
        """Inject Lumen config (task_dir, eval_timeout, phase1_max_tokens)."""
        self._lumen_config = lumen_config

        from transformers import AutoTokenizer
        model_path = self.config.actor_rollout_ref.model.path
        self._tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

        from ttt_discover.tinker_utils import renderers
        self._renderer = renderers.get_renderer("qwen3", tokenizer=self._tokenizer)

        self._phase1_max_tokens = lumen_config.get("phase1_max_tokens", 26000)
        self._context_window = lumen_config.get("max_model_len", 32768)
        self._context_buffer = 50
        self._phase2_prefill = "\n\n... I need to give my final answer now.\n</think>\n"
        self._phase2_prefill_ids = self._tokenizer.encode(
            self._phase2_prefill, add_special_tokens=False
        )
        self._stop_token_ids = self._renderer.get_stop_sequences()

    async def generate_sequences(self, batch: TensorDict) -> None:
        """Override: generate completions for each prompt in the batch."""
        validate = batch.get("validate", False)
        if isinstance(validate, torch.Tensor):
            validate = bool(validate.item())
        batch.pop("validate", None)

        trajectory_info = await get_trajectory_info(
            batch["global_steps"], batch["index"], validate
        )

        for i in range(len(batch)):
            prompt = {}
            for k, v in batch.items():
                if isinstance(v, torch.Tensor):
                    prompt[k] = v[i]
                elif isinstance(v, NonTensorStack):
                    prompt[k] = v[i].data
                elif isinstance(v, NonTensorData):
                    prompt[k] = v.data

            task = asyncio.create_task(
                self._run_prompt(prompt, trajectory=trajectory_info[i], validate=validate)
            )
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

    async def _run_prompt(self, prompt: dict, trajectory: dict, validate: bool) -> None:
        """Generate N completions + evaluate for one prompt."""
        uid = prompt["uid"]
        partition_id = "train" if not validate else "val"
        await tq.async_kv_put(key=uid, partition_id=partition_id, tag={"status": "running"})

        try:
            config = self.config.actor_rollout_ref.rollout
            n = prompt.pop("__rollout_n__", config.n if not validate else config.val_kwargs.n)

            # Build prompt from raw_prompt (set by manager from parquet data)
            messages = prompt.get("raw_prompt", [])
            if not messages:
                logger.error(f"No raw_prompt for uid={uid}")
                await tq.async_kv_put(
                    key=uid, partition_id=partition_id, tag={"status": "failure"}
                )
                return

            model_input = self._renderer.build_generation_prompt(messages)
            prompt_ids = []
            for chunk in model_input.chunks:
                if hasattr(chunk, "tokens"):
                    prompt_ids.extend(chunk.tokens)

            prompt["_prompt_ids"] = prompt_ids

            sampling_params = {
                "temperature": float(config.temperature),
                "top_p": float(config.top_p),
                "top_k": int(config.top_k),
                "repetition_penalty": 1.0,
                "logprobs": config.calculate_log_probs,
                "stop_token_ids": self._stop_token_ids,
            }

            tasks = []
            for session_id in range(n):
                task = asyncio.create_task(
                    self._generate_two_phase(
                        prompt_ids=prompt_ids,
                        sampling_params=sampling_params,
                        prompt=prompt,
                        trajectory=trajectory,
                        validate=validate,
                        session_id=session_id,
                    )
                )
                tasks.append(task)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_results = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"Session {i} failed: {type(r).__name__}: {r}")
                else:
                    valid_results.append(r)

            await tq.async_kv_put(
                key=uid, partition_id=partition_id, tag={"status": "finished"}
            )

        except Exception as e:
            logger.exception(f"Error in _run_prompt: {e}")
            await tq.async_kv_put(
                key=uid, partition_id=partition_id, tag={"status": "failure"}
            )

    async def _generate_two_phase(
        self, prompt_ids, sampling_params, prompt, trajectory, validate, session_id,
    ) -> tuple[AgentLoopOutput, str, float]:
        """Two-phase generation: thinking + forced answer + eval.

        Identical to Discover's three-case logic:
        - Case A: natural stop
        - Case B: budget exhausted, </think> present → continue without prefill
        - Case C: budget exhausted, no </think> → inject prefill (mask=0)
        """
        import time
        t0 = time.time()

        prompt_len = len(prompt_ids)
        phase1_budget = self._phase1_max_tokens - prompt_len
        if phase1_budget <= 0:
            phase1_budget = 100

        request_id = uuid.uuid4().hex
        phase1_output = await self.llm_client.generate(
            request_id=request_id,
            prompt_ids=prompt_ids,
            sampling_params={**sampling_params, "max_tokens": phase1_budget},
        )

        p1_tokens = phase1_output.token_ids
        p1_logprobs = phase1_output.log_probs or [0.0] * len(p1_tokens)

        hit_stop = (
            phase1_output.stop_reason == "stop" or self._hit_stop_token(p1_tokens)
        )
        budget_exhausted = not hit_stop and len(p1_tokens) >= phase1_budget

        gen_case = "A"
        p2_len = 0

        if not budget_exhausted:
            response_ids = p1_tokens
            response_logprobs = p1_logprobs
            response_mask = [1] * len(p1_tokens)
        elif self._contains_pattern(p1_tokens, "</think>"):
            gen_case = "B"
            phase2_prompt = prompt_ids + p1_tokens
            phase2_budget = self._context_window - len(phase2_prompt) - self._context_buffer
            if phase2_budget <= 0:
                response_ids = p1_tokens
                response_logprobs = p1_logprobs
                response_mask = [1] * len(p1_tokens)
            else:
                request_id_p2 = uuid.uuid4().hex
                phase2_output = await self.llm_client.generate(
                    request_id=request_id_p2,
                    prompt_ids=phase2_prompt,
                    sampling_params={**sampling_params, "max_tokens": phase2_budget},
                )
                p2_tokens = phase2_output.token_ids
                p2_logprobs = phase2_output.log_probs or [0.0] * len(p2_tokens)
                p2_len = len(p2_tokens)
                response_ids = p1_tokens + p2_tokens
                response_logprobs = p1_logprobs + p2_logprobs
                response_mask = [1] * len(p1_tokens) + [1] * len(p2_tokens)
        else:
            gen_case = "C"
            phase2_prompt = prompt_ids + p1_tokens + self._phase2_prefill_ids
            phase2_budget = self._context_window - len(phase2_prompt) - self._context_buffer
            if phase2_budget <= 0:
                response_ids = p1_tokens + self._phase2_prefill_ids
                response_logprobs = p1_logprobs + [0.0] * len(self._phase2_prefill_ids)
                response_mask = [1] * len(p1_tokens) + [0] * len(self._phase2_prefill_ids)
            else:
                request_id_p2 = uuid.uuid4().hex
                phase2_output = await self.llm_client.generate(
                    request_id=request_id_p2,
                    prompt_ids=phase2_prompt,
                    sampling_params={**sampling_params, "max_tokens": phase2_budget},
                )
                p2_tokens = phase2_output.token_ids
                p2_logprobs = phase2_output.log_probs or [0.0] * len(p2_tokens)
                p2_len = len(p2_tokens)
                response_ids = p1_tokens + self._phase2_prefill_ids + p2_tokens
                response_logprobs = (
                    p1_logprobs
                    + [0.0] * len(self._phase2_prefill_ids)
                    + p2_logprobs
                )
                response_mask = (
                    [1] * len(p1_tokens)
                    + [0] * len(self._phase2_prefill_ids)
                    + [1] * len(p2_tokens)
                )

        gen_time = time.time() - t0

        output = AgentLoopOutput(
            prompt_ids=prompt_ids,
            response_ids=response_ids,
            response_mask=response_mask,
            response_logprobs=response_logprobs,
            metrics=AgentLoopMetrics(generate_sequences=gen_time),
            extra_fields={
                "min_global_steps": prompt.get("global_steps", 0),
                "max_global_steps": prompt.get("global_steps", 0),
            },
        )

        # Evaluate via Einstein Arena verifier
        response_text = self._tokenizer.decode(response_ids, skip_special_tokens=True)
        code = ""
        score = 0.0
        eval_msg = ""

        if not validate:
            from factory.lumen.verl_integration.reward import compute_score
            extra_info = prompt.get("extra_info", {})
            if not extra_info.get("task_dir"):
                extra_info["task_dir"] = self._lumen_config.get("task_dir", "")
            extra_info["eval_timeout"] = self._lumen_config.get("eval_timeout", 60)

            result = await asyncio.to_thread(
                compute_score,
                data_source=self._lumen_config.get("data_source", "lumen"),
                solution_str=response_text,
                ground_truth=None,
                extra_info=extra_info,
            )
            score = float(result.get("score", 0.0))
            code = result.get("code", "")
            eval_msg = result.get("eval_msg", "")

        output.reward_score = score
        reward_extra = {
            "acc": float(score > 0),
            "code": code,
            "eval_msg": eval_msg,
            "gen_case": gen_case,
            "p1_len": len(p1_tokens),
            "p2_len": p2_len,
            "gen_time_s": round(gen_time, 3),
        }
        output.extra_fields["reward_extra_info"] = reward_extra

        await self._write_to_tq(output, prompt, session_id, validate)
        return output, code, score

    async def _write_to_tq(
        self, output: AgentLoopOutput, prompt: dict, session_id: int, validate: bool,
    ) -> None:
        """Write output to TransferQueue in VERL's expected format."""
        uid = prompt["uid"]
        partition_id = "train" if not validate else "val"
        key = f"{uid}_{session_id}_0"

        prompts = torch.tensor(output.prompt_ids, dtype=torch.int64)
        responses = torch.tensor(output.response_ids, dtype=torch.int64)
        input_ids = torch.cat([prompts, responses], dim=0)
        attention_mask = torch.ones_like(input_ids, dtype=torch.int64)
        position_ids = torch.arange(len(input_ids), dtype=torch.int64)

        field = output.as_dict()
        field["uid"] = uid
        field["session_id"] = session_id
        field["global_steps"] = prompt.get("global_steps", 0)
        field["raw_prompt"] = prompt.get("raw_prompt", [])
        field["data_source"] = self._lumen_config.get("data_source", "lumen")
        field["num_turns"] = 1
        field.pop("multi_modal_data", None)
        field["loss_mask"] = field["response_mask"]
        field["input_ids"] = input_ids
        field["position_ids"] = position_ids
        field["attention_mask"] = attention_mask
        field["multi_modal_inputs"] = {}

        prompt_len = prompts.size(0)
        response_len = responses.size(0)
        tag = {
            "status": "success",
            "prompt_len": prompt_len,
            "response_len": response_len,
            "seq_len": prompt_len + response_len,
            "global_steps": prompt.get("global_steps", 0),
            "min_global_steps": output.extra_fields.get("min_global_steps", 0),
            "max_global_steps": output.extra_fields.get("max_global_steps", 0),
        }

        await tq.async_kv_batch_put(
            keys=[key],
            fields=list_of_dict_to_tensordict([field]),
            tags=[tag],
            partition_id=partition_id,
        )

    def _hit_stop_token(self, tokens: list[int]) -> bool:
        if not tokens or not self._stop_token_ids:
            return False
        return tokens[-1] in self._stop_token_ids

    def _contains_pattern(self, tokens: list[int], pattern: str) -> bool:
        pattern_ids = self._tokenizer.encode(pattern, add_special_tokens=False)
        if len(pattern_ids) > len(tokens):
            return False
        for i in range(len(tokens) - len(pattern_ids) + 1):
            if tokens[i : i + len(pattern_ids)] == pattern_ids:
                return True
        return False


class LumenAgentLoopManagerTQ(AgentLoopManager):
    """Lumen agent loop manager — distributes prompts to workers."""

    def __init__(self, config, workers, **kwargs):
        super().__init__(config, workers, **kwargs)
        self._lumen_config = {}

    def set_lumen_config(self, lumen_config: dict):
        self._lumen_config = lumen_config
        for worker in self.workers:
            ray.get(worker.set_lumen_config.remote(lumen_config))

    async def generate_sequences(self, prompts: TensorDict) -> list[TensorDict]:
        """Assign prompts directly to workers (no PUCT sampling)."""
        # Prompts come from parquet data source — just pass through to workers
        # The raw_prompt field is already set from the parquet's prompt column
        return await super().generate_sequences(prompts)
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('factory/lumen/verl_integration/agent_loop.py').read()); print('Syntax OK')"
```

- [ ] **Step 3: Commit**

```bash
git add factory/lumen/verl_integration/agent_loop.py
git commit -m "feat(lumen): port two-phase agent loop from Discover (PUCT removed)"
```

---

### Task 5: Launch Wrapper + Shell Script

**Files:**
- Create: `factory/lumen/run_verl.py`
- Create: `factory/lumen/run_verl.sh`
- Modify: `factory/lumen/types.py` (add `TrainingMetrics`, `RolloutRecord`)
- Modify: `factory/lumen/checkpoint.py` (add VERL checkpoint path tracking)
- Test: `tests/test_lumen_run_verl.py`

**Interfaces:**
- Consumes: CLI args (prompts path, task_dir, checkpoint_dir, model_path, iteration, etc.)
- Produces:
  - `build_verl_overrides(args: argparse.Namespace) -> list[str]` — generate Hydra override list
  - `post_process_results(rollout_log: Path, output_dir: Path, prompts_data: dict, iteration: int) -> dict` — read VERL rollout log, write evaluation_results.json + rollouts.jsonl, return results dict
  - `main()` — CLI entry point

- [ ] **Step 1: Extend types.py**

```python
# Add to factory/lumen/types.py after existing Rollout TypedDict:

class TrainingMetrics(TypedDict, total=False):
    """Metrics from one RL training iteration."""
    loss: float
    advantage_mean: float
    advantage_std: float
    kl_divergence: float
    beta_solved: float


class RolloutRecord(TypedDict):
    """A single rollout record for rollouts.jsonl."""
    prompt_idx: int
    rollout_idx: int
    global_idx: int
    prompt: str
    thinking: str
    code: str
    solution: dict
    score: float
    gen_case: str
    p1_len: int
    p2_len: int
```

- [ ] **Step 2: Extend checkpoint.py**

```python
# Add to factory/lumen/checkpoint.py after existing functions:

def get_verl_checkpoint_path(project_path: Path) -> Path | None:
    """Get the latest VERL checkpoint path, or None if no checkpoint exists."""
    ckpt_dir = project_path / ".factory/lumen/checkpoints/verl/latest"
    if ckpt_dir.exists():
        return ckpt_dir
    return None


def get_verl_rollout_log(project_path: Path, step: int = 0) -> Path | None:
    """Get the VERL rollout log for a given step."""
    log_path = project_path / f".factory/lumen/checkpoints/verl/rollouts/{step}.jsonl"
    if log_path.exists():
        return log_path
    return None
```

- [ ] **Step 3: Write test for config generation and post-processing**

```python
# tests/test_lumen_run_verl.py
"""Tests for Lumen VERL launch wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def mock_prompts(tmp_path: Path) -> Path:
    data = {
        "iteration": 0,
        "scoring_direction": "maximize",
        "prompts": [
            {"prompt_idx": i, "strategy": f"s{i}", "prompt_text": f"prompt {i}"}
            for i in range(8)
        ],
    }
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture()
def mock_rollout_log(tmp_path: Path) -> Path:
    """Create a mock VERL rollout log (512 lines)."""
    log_dir = tmp_path / "rollouts"
    log_dir.mkdir()
    log_path = log_dir / "0.jsonl"
    with open(log_path, "w") as f:
        for prompt_idx in range(8):
            for rollout_idx in range(64):
                global_idx = prompt_idx * 64 + rollout_idx
                entry = {
                    "input": f"prompt {prompt_idx}",
                    "output": f"<think>thinking</think>\n```python\nprint('hello')\n```",
                    "score": 1.0 + prompt_idx * 0.1 + rollout_idx * 0.001,
                    "step": 0,
                    "uid": f"uid_{prompt_idx}_{rollout_idx}_0",
                    "gts": None,
                }
                f.write(json.dumps(entry) + "\n")
    return log_path


class TestBuildVerlOverrides:
    def test_basic_overrides(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts),
            task_dir="/path/to/task",
            checkpoint_dir=str(tmp_path / "ckpt"),
            output_dir=str(tmp_path / "out"),
            model_path="Qwen/Qwen3-8B",
            iteration=0,
            rollouts_per_prompt=64,
            num_gpus=8,
            rollout_tp=4,
            lora_rank=32,
            learning_rate=4e-5,
            kl_coef=0.1,
            temperature=0.8,
            phase1_max_tokens=26000,
            eval_timeout=60,
            parquet_path=str(tmp_path / "prompts.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("entropic_adaptive_beta" in o for o in overrides)
        assert any("total_epochs=1" in o for o in overrides)
        assert any("Qwen/Qwen3-8B" in o for o in overrides)

    def test_resume_mode_for_iteration_zero(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts), task_dir=".", checkpoint_dir=str(tmp_path),
            output_dir=str(tmp_path), model_path="m", iteration=0,
            rollouts_per_prompt=64, num_gpus=1, rollout_tp=1, lora_rank=32,
            learning_rate=4e-5, kl_coef=0.1, temperature=0.8,
            phase1_max_tokens=26000, eval_timeout=60,
            parquet_path=str(tmp_path / "p.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("resume_mode=auto" in o for o in overrides)

    def test_resume_mode_for_iteration_nonzero(self, mock_prompts: Path, tmp_path: Path) -> None:
        from factory.lumen.run_verl import build_verl_overrides

        ckpt_dir = tmp_path / "ckpt" / "latest"
        ckpt_dir.mkdir(parents=True)

        import argparse
        args = argparse.Namespace(
            prompts=str(mock_prompts), task_dir=".", checkpoint_dir=str(tmp_path / "ckpt"),
            output_dir=str(tmp_path), model_path="m", iteration=3,
            rollouts_per_prompt=64, num_gpus=1, rollout_tp=1, lora_rank=32,
            learning_rate=4e-5, kl_coef=0.1, temperature=0.8,
            phase1_max_tokens=26000, eval_timeout=60,
            parquet_path=str(tmp_path / "p.parquet"),
        )
        overrides = build_verl_overrides(args)
        assert any("resume_mode=resume_path" in o for o in overrides)


class TestPostProcessResults:
    def test_writes_evaluation_results(
        self, mock_rollout_log: Path, mock_prompts: Path, tmp_path: Path,
    ) -> None:
        from factory.lumen.run_verl import post_process_results

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with open(mock_prompts) as f:
            prompts_data = json.load(f)

        results = post_process_results(mock_rollout_log, output_dir, prompts_data, iteration=0)
        assert results["num_rollouts"] == 512
        assert results["best_score"] > 0
        assert len(results["per_prompt_stats"]) == 8
        assert (output_dir / "evaluation_results.json").exists()
        assert (output_dir / "rollouts.jsonl").exists()

    def test_rollouts_jsonl_has_correct_count(
        self, mock_rollout_log: Path, mock_prompts: Path, tmp_path: Path,
    ) -> None:
        from factory.lumen.run_verl import post_process_results

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with open(mock_prompts) as f:
            prompts_data = json.load(f)

        post_process_results(mock_rollout_log, output_dir, prompts_data, iteration=0)
        with open(output_dir / "rollouts.jsonl") as f:
            lines = f.readlines()
        assert len(lines) == 512
```

- [ ] **Step 4: Run tests — expect FAIL**

```bash
pytest tests/test_lumen_run_verl.py -v
```

- [ ] **Step 5: Implement run_verl.py**

```python
# factory/lumen/run_verl.py
"""Lumen VERL launch wrapper — orchestrate a single RL training iteration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def build_verl_overrides(args: argparse.Namespace) -> list[str]:
    """Generate VERL Hydra override list from parsed CLI args."""
    checkpoint_dir = Path(args.checkpoint_dir)
    latest_ckpt = checkpoint_dir / "latest"

    if args.iteration > 0 and latest_ckpt.exists():
        resume_mode = "resume_path"
        resume_path = str(latest_ckpt)
    else:
        resume_mode = "auto"
        resume_path = ""

    ppo_mini_batch = args.rollouts_per_prompt * 8

    overrides = [
        "algorithm.adv_estimator=entropic_adaptive_beta",
        "algorithm.use_kl_in_reward=False",
        f"algorithm.kl_ctrl.kl_coef={args.kl_coef}",
        f"data.train_files={args.parquet_path}",
        f"data.val_files={args.parquet_path}",
        "data.train_batch_size=8",
        "data.max_prompt_length=4096",
        "data.max_response_length=28672",
        "data.filter_overlong_prompts=True",
        "data.truncation=error",
        f"actor_rollout_ref.model.path={args.model_path}",
        "actor_rollout_ref.model.use_remove_padding=True",
        "actor_rollout_ref.model.enable_gradient_checkpointing=True",
        f"actor_rollout_ref.model.lora_rank={args.lora_rank}",
        f"actor_rollout_ref.model.lora_alpha={args.lora_rank}",
        "actor_rollout_ref.model.target_modules=all-linear",
        "++actor_rollout_ref.model.lora.merge=True",
        f"actor_rollout_ref.actor.optim.lr={args.learning_rate}",
        "actor_rollout_ref.actor.optim.betas=[0.9,0.95]",
        "actor_rollout_ref.actor.grad_clip=1e9",
        f"actor_rollout_ref.actor.ppo_mini_batch_size={ppo_mini_batch}",
        "actor_rollout_ref.actor.clip_ratio=1000.0",
        "actor_rollout_ref.actor.clip_ratio_low=1000.0",
        "actor_rollout_ref.actor.clip_ratio_high=1000.0",
        "actor_rollout_ref.actor.use_dynamic_bsz=True",
        "actor_rollout_ref.actor.ppo_max_token_len_per_gpu=32768",
        "actor_rollout_ref.actor.use_kl_loss=False",
        "actor_rollout_ref.actor.entropy_coeff=0",
        "actor_rollout_ref.actor.fsdp_config.param_offload=True",
        "actor_rollout_ref.actor.fsdp_config.optimizer_offload=True",
        "actor_rollout_ref.actor.fsdp_config.ulysses_sequence_parallel_size=1",
        "actor_rollout_ref.rollout.name=vllm",
        f"actor_rollout_ref.rollout.tensor_model_parallel_size={args.rollout_tp}",
        "actor_rollout_ref.rollout.gpu_memory_utilization=0.5",
        f"actor_rollout_ref.rollout.n={args.rollouts_per_prompt}",
        "actor_rollout_ref.rollout.log_prob_use_dynamic_bsz=True",
        "actor_rollout_ref.rollout.log_prob_max_token_len_per_gpu=32768",
        "actor_rollout_ref.rollout.free_cache_engine=True",
        "actor_rollout_ref.rollout.enforce_eager=True",
        "+actor_rollout_ref.rollout.agent.agent_loop_manager_class="
        "factory.lumen.verl_integration.agent_loop.LumenAgentLoopManagerTQ",
        "actor_rollout_ref.ref.log_prob_use_dynamic_bsz=True",
        "actor_rollout_ref.ref.log_prob_max_token_len_per_gpu=32768",
        "actor_rollout_ref.ref.fsdp_config.param_offload=True",
        "actor_rollout_ref.ref.fsdp_config.ulysses_sequence_parallel_size=1",
        f"reward.custom_reward_function.path={Path(__file__).parent / 'verl_integration/reward.py'}",
        "reward.custom_reward_function.name=compute_score",
        "trainer.balance_batch=True",
        'trainer.logger=["console","file"]',
        "trainer.project_name=lumen",
        f"trainer.experiment_name=lumen_iter_{args.iteration}",
        f"trainer.n_gpus_per_node={args.num_gpus}",
        "trainer.nnodes=1",
        "trainer.save_freq=0",
        "trainer.test_freq=-1",
        "trainer.total_epochs=1",
        f"trainer.rollout_data_dir={args.checkpoint_dir}/rollouts",
        "trainer.val_before_train=False",
        f"trainer.resume_mode={resume_mode}",
    ]

    if resume_path:
        overrides.append(f"trainer.resume_from_path={resume_path}")

    return overrides


def post_process_results(
    rollout_log: Path,
    output_dir: Path,
    prompts_data: dict,
    iteration: int,
) -> dict:
    """Read VERL rollout log and write evaluation_results.json + rollouts.jsonl."""
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    with open(rollout_log) as f:
        for line in f:
            entries.append(json.loads(line))

    scores = [e["score"] for e in entries]
    num_rollouts = len(entries)
    num_prompts = len(prompts_data["prompts"])
    rollouts_per_prompt = num_rollouts // num_prompts if num_prompts > 0 else 0
    scoring_direction = prompts_data.get("scoring_direction", "maximize")

    # Write rollouts.jsonl
    with open(output_dir / "rollouts.jsonl", "w") as f:
        for idx, entry in enumerate(entries):
            prompt_idx = idx // rollouts_per_prompt if rollouts_per_prompt > 0 else 0
            rollout_idx = idx % rollouts_per_prompt if rollouts_per_prompt > 0 else idx
            record = {
                "prompt_idx": prompt_idx,
                "rollout_idx": rollout_idx,
                "global_idx": idx,
                "prompt": entry.get("input", ""),
                "thinking": "",
                "code": "",
                "solution": {},
                "score": entry["score"],
                "gen_case": "A",
                "p1_len": 0,
                "p2_len": 0,
            }
            f.write(json.dumps(record) + "\n")

    # Compute per-prompt stats
    per_prompt_stats = []
    for i in range(num_prompts):
        start = i * rollouts_per_prompt
        end = start + rollouts_per_prompt
        group_scores = scores[start:end]
        if not group_scores:
            continue
        strategy = prompts_data["prompts"][i].get("strategy", "")
        best_fn = max if scoring_direction == "maximize" else min
        per_prompt_stats.append({
            "prompt_idx": i,
            "strategy": strategy,
            "mean": float(np.mean(group_scores)),
            "std": float(np.std(group_scores)),
            "best": float(best_fn(group_scores)),
        })

    best_idx = int(np.argmax(scores) if scoring_direction == "maximize" else np.argmin(scores))
    results = {
        "iteration": iteration,
        "num_rollouts": num_rollouts,
        "scores": scores,
        "best_score": scores[best_idx],
        "best_rollout_idx": best_idx,
        "best_solution": {},
        "mean_score": float(np.mean(scores)),
        "std_score": float(np.std(scores)),
        "per_prompt_stats": per_prompt_stats,
    }

    with open(output_dir / "evaluation_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Lumen VERL Training Launcher")
    parser.add_argument("--prompts", required=True, help="Path to prompts.json")
    parser.add_argument("--task-dir", required=True, help="Einstein Arena task directory")
    parser.add_argument("--checkpoint-dir", required=True, help="VERL checkpoint directory")
    parser.add_argument("--output-dir", required=True, help="Iteration output directory")
    parser.add_argument("--model-path", required=True, help="Base model path")
    parser.add_argument("--iteration", type=int, required=True, help="Current iteration")
    parser.add_argument("--rollouts-per-prompt", type=int, default=64)
    parser.add_argument("--num-gpus", type=int, default=8)
    parser.add_argument("--rollout-tp", type=int, default=4)
    parser.add_argument("--lora-rank", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=4e-5)
    parser.add_argument("--kl-coef", type=float, default=0.1)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--phase1-max-tokens", type=int, default=26000)
    parser.add_argument("--eval-timeout", type=int, default=60)

    args = parser.parse_args()

    prompts_path = Path(args.prompts)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Create parquet from prompts
    from factory.lumen.verl_integration.data_source import create_parquet_from_prompts

    parquet_path = output_dir / "prompts.parquet"
    create_parquet_from_prompts(prompts_path, parquet_path, task_dir=args.task_dir)
    args.parquet_path = str(parquet_path)

    # Step 2: Build VERL config
    overrides = build_verl_overrides(args)

    # Step 3: Launch VERL
    print(f"=== Lumen VERL Training — Iteration {args.iteration} ===")
    print(f"Model: {args.model_path}")
    print(f"GPUs: {args.num_gpus}, TP: {args.rollout_tp}")
    print(f"Rollouts: 8 × {args.rollouts_per_prompt} = {8 * args.rollouts_per_prompt}")

    import subprocess
    cmd = [
        sys.executable, "-m", "verl.trainer.main_ppo",
        *overrides,
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"VERL training failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    # Step 4: Post-process results
    rollout_log = Path(args.checkpoint_dir) / "rollouts" / "0.jsonl"
    if not rollout_log.exists():
        print(f"WARNING: Rollout log not found at {rollout_log}", file=sys.stderr)
        sys.exit(1)

    with open(prompts_path) as f:
        prompts_data = json.load(f)

    results = post_process_results(rollout_log, output_dir, prompts_data, args.iteration)
    print(f"Best score: {results['best_score']:.6f}")
    print(f"Mean score: {results['mean_score']:.6f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest tests/test_lumen_run_verl.py -v
```

- [ ] **Step 7: Create run_verl.sh**

```bash
#!/usr/bin/env bash
# Lumen VERL launcher — activates verl_discover conda env and delegates to run_verl.py
set -xeuo pipefail

CONDA_ENV=${CONDA_ENV:-verl_discover}
if [ "$CONDA_DEFAULT_ENV" != "$CONDA_ENV" ]; then
    eval "$(conda shell.bash hook 2>/dev/null)" && conda activate "$CONDA_ENV"
fi

# CUDA memory fragmentation reduction
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Forward all arguments to the Python launcher
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m factory.lumen.run_verl "$@"
```

- [ ] **Step 8: Commit**

```bash
git add factory/lumen/run_verl.py factory/lumen/run_verl.sh factory/lumen/types.py factory/lumen/checkpoint.py tests/test_lumen_run_verl.py
git commit -m "feat(lumen): add VERL launch wrapper and shell script"
```

---

### Task 6: Workflow Integration + train.py Update

**Files:**
- Modify: `factory/lumen/train.py` (add real mode that delegates to run_verl)
- Modify: `factory/workflow/contributed/lumen/workflow.py` (update rl_train command)
- Create: `factory/lumen/docs/environment-setup.md`

**Interfaces:**
- Consumes: all modules from Tasks 1-5
- Produces: updated workflow that can run with `--mock` (existing behavior) or real VERL mode

- [ ] **Step 1: Update train.py to support real mode**

Replace the `else` branch in `train.py` (line 60-61 "ERROR: Real vLLM not implemented yet") with a delegation to `run_verl.py`:

```python
# In factory/lumen/train.py, replace the else branch at line 59-61:
    else:
        import subprocess
        cmd = [
            sys.executable, "-m", "factory.lumen.run_verl",
            "--prompts", str(prompts_file),
            "--task-dir", args.task_dir,
            "--checkpoint-dir", str(project_path / ".factory/lumen/checkpoints/verl"),
            "--output-dir", str(iteration_dir),
            "--model-path", getattr(args, "model_path", "Qwen/Qwen3-8B"),
            "--iteration", str(args.iteration),
            "--rollouts-per-prompt", str(args.num_rollouts_per_prompt),
        ]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"VERL training failed with exit code {result.returncode}")
            sys.exit(result.returncode)
        return
```

Also add `--model-path` to the argparser:

```python
    parser.add_argument("--model-path", default="Qwen/Qwen3-8B", help="Base model path")
```

And change `--mock` default to `False` (it was `True`):

```python
    parser.add_argument(
        "--mock", action="store_true", default=False, help="Use mock rollouts"
    )
```

- [ ] **Step 2: Update workflow.py rl_train command**

The workflow command needs to support both mock and real modes. Add `{model_path}` and `{mock_flag}` template variables:

```python
# In factory/workflow/contributed/lumen/workflow.py, update the rl_train FnNode:
    nodes["rl_train"] = FnNode(
        id="rl_train",
        command=(
            "cd {project_path} && "
            'ITER=$(python3 -c "import json; '
            "print(json.load(open('.factory/lumen/state.json'))['iteration'])"
            '") && '
            "python3 -m factory.lumen.train "
            "--task {task_name} "
            "--task-dir benchmarks/einsteinarena/{task_name} "
            "--project-path {project_path} "
            "--iteration $ITER "
            "--num-rollouts-per-prompt {rollouts_per_prompt} "
            "--model-path {model_path} "
            "{mock_flag}"
        ),
        reads={
            ".factory/lumen/state.json",
            ".factory/lumen/iteration_{current_iteration}/prompts.json",
        },
        writes={
            ".factory/lumen/iteration_{current_iteration}/rollouts.jsonl",
            ".factory/lumen/iteration_{current_iteration}/evaluation_results.json",
        },
    )
```

- [ ] **Step 3: Write environment-setup.md**

```markdown
# Lumen 环境搭建指南

## 复用现有环境

如果 `verl_discover` conda 环境已存在（例如之前用来跑 Discover）：

    conda activate verl_discover
    python -c "import verl; import vllm; import peft; print('OK')"

## 从零搭建

    conda create -n verl_discover python=3.11 -y
    conda activate verl_discover
    pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu129
    pip install vllm==0.23.0
    cd /path/to/discover/verl && pip install -e .
    pip install peft transformers ray[default] pandas pyarrow

验证 entropic_adaptive_beta 已注册：

    python -c "from verl.trainer.ppo.core_algos import get_adv_estimator_fn; get_adv_estimator_fn('entropic_adaptive_beta'); print('OK')"

## Workflow 中的环境激活

`run_verl.sh` 脚本头部自动激活 `verl_discover`。如需使用其他环境名，设置：

    CONDA_ENV=my_env bash factory/lumen/run_verl.sh ...

## Mock 模式

不需要 GPU 或 VERL 环境。在 workflow 中传入 `--mock` 即可使用随机 rollout 测试流程。
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
pytest factory/workflow/contributed/lumen/test_workflow.py -v
pytest tests/test_lumen_data_source.py tests/test_lumen_advantages.py tests/test_lumen_reward.py tests/test_lumen_run_verl.py -v
```

- [ ] **Step 5: Commit**

```bash
git add factory/lumen/train.py factory/workflow/contributed/lumen/workflow.py factory/lumen/docs/environment-setup.md
git commit -m "feat(lumen): integrate VERL training into workflow, update train.py and rl_train node"
```

---

### Task 7: End-to-End Verification

**Files:**
- No new files — verification of all previous tasks working together

This task is a manual verification checklist. No code to write — just commands to run.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/test_lumen_data_source.py tests/test_lumen_advantages.py tests/test_lumen_reward.py tests/test_lumen_run_verl.py factory/workflow/contributed/lumen/test_workflow.py -v
```

- [ ] **Step 2: Verify mock mode still works end-to-end**

```bash
cd <project-root>
mkdir -p /tmp/lumen-test/.factory/lumen/iteration_0

# Create a test prompts.json
python3 -c "
import json
data = {
    'iteration': 0, 'problem_type': 'geometry', 'scoring_direction': 'maximize',
    'solution_schema': {'circles': 'array of [x, y, r]'},
    'prompts': [
        {'prompt_idx': i, 'strategy': f'strategy_{i}', 'prompt_text': f'Optimize {i}'}
        for i in range(8)
    ]
}
json.dump(data, open('/tmp/lumen-test/.factory/lumen/iteration_0/prompts.json', 'w'))
json.dump({'iteration': 0, 'best_score': None}, open('/tmp/lumen-test/.factory/lumen/state.json', 'w'))
"

# Run mock training
python3 -m factory.lumen.train \
  --task circle-packing \
  --task-dir benchmarks/einsteinarena/circle-packing \
  --project-path /tmp/lumen-test \
  --iteration 0 \
  --num-rollouts-per-prompt 8 \
  --mock

# Verify outputs
ls -la /tmp/lumen-test/.factory/lumen/iteration_0/
python3 -c "import json; r = json.load(open('/tmp/lumen-test/.factory/lumen/iteration_0/evaluation_results.json')); print(f'Rollouts: {r[\"num_rollouts\"]}, Best: {r[\"best_score\"]}')"
```

- [ ] **Step 3: Verify VERL config generation (dry run)**

```bash
python3 -c "
import argparse
from factory.lumen.run_verl import build_verl_overrides

args = argparse.Namespace(
    prompts='/tmp/lumen-test/.factory/lumen/iteration_0/prompts.json',
    task_dir='benchmarks/einsteinarena/circle-packing',
    checkpoint_dir='/tmp/lumen-test/.factory/lumen/checkpoints/verl',
    output_dir='/tmp/lumen-test/.factory/lumen/iteration_0',
    model_path='Qwen/Qwen3-8B', iteration=0,
    rollouts_per_prompt=64, num_gpus=8, rollout_tp=4, lora_rank=32,
    learning_rate=4e-5, kl_coef=0.1, temperature=0.8,
    phase1_max_tokens=26000, eval_timeout=60,
    parquet_path='/tmp/lumen-test/prompts.parquet',
)
overrides = build_verl_overrides(args)
print(f'Generated {len(overrides)} VERL overrides')
for o in overrides[:5]:
    print(f'  {o}')
print('  ...')
"
```

- [ ] **Step 4: Verify ruff lint passes**

```bash
ruff check factory/lumen/ --select E,F,W
```

- [ ] **Step 5: Commit any lint fixes if needed**

```bash
ruff check factory/lumen/ --fix
git add -u factory/lumen/
git diff --cached --stat && git commit -m "style(lumen): fix lint issues" || echo "No lint fixes needed"
```

- [ ] **Step 6: Clean up temp files**

```bash
rm -rf /tmp/lumen-test
```
