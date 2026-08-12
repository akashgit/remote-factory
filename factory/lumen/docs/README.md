# Lumen Documentation

Lumen is the RL training module for Einstein Arena benchmarks.

## Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Architecture](#architecture)
- [API Reference](#api-reference)

## Overview

Lumen provides RL-based optimization for Einstein Arena mathematical optimization problems. It uses iterative prompt refinement to improve solution quality.

## Getting Started

```bash
# Run Lumen training on a specific task
python3 -m factory.lumen.train --task circle-packing --mock --iterations 3

# Run via workflow
factory workflow run lumen /path/to/project
```

## Architecture

- `train.py` - Main training loop
- `evaluate.py` - Solution evaluation using Harbor verifier
- `mock_rollout.py` - Mock rollout generator for testing
- `checkpoint.py` - State management
- `types.py` - Type definitions

## API Reference

See individual module docstrings for detailed API documentation.
