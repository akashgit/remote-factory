# Lumen 环境搭建指南

## 复用现有环境

如果 `lumen` conda 环境已存在：

    conda activate lumen
    python -c "import verl; import vllm; import peft; print('OK')"

## 从零搭建

    conda create -n lumen python=3.11 -y
    conda activate lumen
    pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu129
    pip install vllm==0.23.0
    cd /path/to/discover/verl && pip install -e .
    pip install peft transformers ray[default] pandas pyarrow

验证 entropic_adaptive_beta 已注册：

    python -c "from verl.trainer.ppo.core_algos import get_adv_estimator_fn; get_adv_estimator_fn('entropic_adaptive_beta'); print('OK')"

## Workflow 中的环境激活

`run_verl.sh` 脚本头部自动激活 `lumen`。如需使用其他环境名，设置：

    CONDA_ENV=my_env bash factory/lumen/run_verl.sh ...

## Mock 模式

不需要 GPU 或 VERL 环境。在 workflow 中传入 `--mock` 即可使用随机 rollout 测试流程。
