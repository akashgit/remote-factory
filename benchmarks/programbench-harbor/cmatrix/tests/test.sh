#!/usr/bin/env bash
set -euo pipefail

cd /workspace

if [ ! -f compile.sh ]; then
    echo "ERROR: compile.sh not found"
    echo '{"reward": 0.0}' > /logs/verifier/reward.json
    exit 0
fi

echo "Running compile.sh..."
if ! bash compile.sh 2>&1; then
    echo "ERROR: compile.sh failed"
    echo '{"reward": 0.0}' > /logs/verifier/reward.json
    exit 0
fi

echo "Packaging submission..."
tar -czf /logs/verifier/submission.tar.gz \
    --exclude=.git --exclude=target \
    --exclude=executable.bak --exclude=./executable \
    --exclude=.factory --exclude=eval --exclude=factory.md .

if [ -f /logs/verifier/submission.tar.gz ]; then
    SIZE=$(du -h /logs/verifier/submission.tar.gz | cut -f1)
    echo "Submission packaged: ${SIZE}"
    echo '{"reward": 1.0}' > /logs/verifier/reward.json
else
    echo "ERROR: Failed to create submission.tar.gz"
    echo '{"reward": 0.0}' > /logs/verifier/reward.json
fi
