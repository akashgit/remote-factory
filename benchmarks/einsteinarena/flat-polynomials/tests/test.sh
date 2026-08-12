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
    coefficients = np.array(data["coefficients"], dtype=np.float64)
    assert len(coefficients) == 70, f"Expected 70 coefficients, got {len(coefficients)}"
    assert all(c in (-1, 1) for c in coefficients), "All coefficients must be +1 or -1"
    poly_fn = np.poly1d(coefficients)
    num_points = 1_000_000
    zs = np.exp(1j * np.linspace(0, 2 * np.pi, num_points))
    vals = np.abs(poly_fn(zs))
    return float(np.max(vals) / np.sqrt(len(coefficients) + 1))

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
