#!/usr/bin/env bash
set -euo pipefail
cd /workspace
pip install pytest -q 2>/dev/null
RESULT=$(python -m pytest test_dedup.py -v 2>&1) || true
if echo "$RESULT" | grep -q 'passed' && ! echo "$RESULT" | grep -q 'failed'; then
    echo '{"reward": 1.0}' > /logs/verifier/reward.json
else
    echo '{"reward": 0.0}' > /logs/verifier/reward.json
fi
echo "$RESULT"
