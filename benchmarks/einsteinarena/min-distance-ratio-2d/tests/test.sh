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

def evaluate(data: dict) -> float:
    vectors = np.array(data["vectors"], dtype=np.float64)
    if vectors.ndim != 2 or vectors.shape[0] != 16 or vectors.shape[1] != 2:
        raise ValueError("Expected exactly 16 points in 2 dimensions, shape (16, 2)")
    n = vectors.shape[0]
    diff = vectors[:, None, :] - vectors[None, :, :]
    dist_matrix = np.sqrt(np.sum(diff**2, axis=-1))
    mask = np.triu(np.ones((n, n), dtype=bool), k=1)
    pairwise = dist_matrix[mask]
    min_d = np.min(pairwise)
    if min_d < 1e-12:
        raise ValueError("Points must be distinct (min distance < 1e-12)")
    max_d = np.max(pairwise)
    return float((max_d / min_d) ** 2)

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
