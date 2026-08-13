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
