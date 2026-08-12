#!/bin/bash
set -euo pipefail

# Einstein Arena verifier wrapper
SOLUTION_FILE="${WORKSPACE:-/workspace}/solution.json"
SCORE_FILE="${WORKSPACE:-/workspace}/score.txt"

if [ ! -f "$SOLUTION_FILE" ]; then
    echo "ERROR: solution.json not found at $SOLUTION_FILE" >&2
    exit 1
fi

# Create verifier script
cat > /tmp/verifier.py << 'VERIFIER_EOF'
import numpy as np

def evaluate(data):
    vectors = np.array(data["vectors"], dtype=np.float64)
    assert vectors.shape == (282, 3), f"Expected (282, 3), got {vectors.shape}"
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1e-12
    vectors = vectors / norms
    diffs = vectors[:, None, :] - vectors[None, :, :]
    dist_sq = np.sum(diffs**2, axis=2)
    iu = np.triu_indices(282, k=1)
    dists = np.sqrt(dist_sq[iu])
    dists[dists < 1e-12] = 1e-12
    return float(np.sum(1.0 / dists))

# Wrapper to read from file and handle errors
if __name__ == "__main__":
    import json
    import os
    import sys

    _ws = os.environ.get("WORKSPACE", "/workspace")

    try:
        with open(f"{_ws}/solution.json", "r") as f:
            data = json.load(f)

        score = evaluate(data)

        # Write score to file
        with open(f"{_ws}/score.txt", "w") as f:
            f.write(str(score))

        print(f"Score: {score}")
        sys.exit(0)

    except Exception as e:
        print(f"Verifier failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
VERIFIER_EOF

# Run verifier
python3 /tmp/verifier.py
EXIT_CODE=$?

if [ -f "$SCORE_FILE" ]; then
    echo "Verification complete. Score: $(cat $SCORE_FILE)"
else
    echo "ERROR: Verifier did not produce a score" >&2
fi

exit $EXIT_CODE
