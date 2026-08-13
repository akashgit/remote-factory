# Lumen Workflow

Lumen RL training workflow for Einstein Arena.

## Quick Links

- **📚 Full Documentation**: [`factory/lumen/README.md`](../../../lumen/README.md)
- **✅ Verification Script**: [`factory/lumen/env_specs/verify_env.py`](../../../lumen/env_specs/verify_env.py)

## Quick Start

```bash
# 1. Install lumen environment
cd factory/lumen
uv sync --no-install-project
uv pip install vllm
git clone https://github.com/ash-ding/verl.git ~/verl
uv pip install -e ~/verl

# 2. Verify installation
.venv/bin/python env_specs/verify_env.py

# 3. Run workflow
factory ceo /path/to/project --mode lumen
```

## Files in this Directory

- `workflow.py` - Workflow graph definition
- `test_workflow.py` - Workflow tests
- `__init__.py` - Package marker

All other Lumen-related code and documentation is in `factory/lumen/`.
