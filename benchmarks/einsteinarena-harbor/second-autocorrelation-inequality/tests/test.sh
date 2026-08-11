#!/bin/bash
set -euo pipefail

# Einstein Arena verifier wrapper
SOLUTION_FILE="/workspace/solution.json"
SCORE_FILE="/workspace/score.txt"

if [ ! -f "$SOLUTION_FILE" ]; then
    echo "ERROR: solution.json not found at $SOLUTION_FILE" >&2
    exit 1
fi

# Create verifier script
cat > /tmp/verifier.py << 'VERIFIER_EOF'
import numpy as np
from scipy.signal import oaconvolve

def verify_and_compute_c2(values: list[float]) -> float:
    f = np.array(values, dtype=np.float64)
    n_points = len(values)
    if f.shape != (n_points,):
        raise ValueError(f"Expected shape ({n_points},), got {f.shape}")
    if np.any(f < -1e-6):
        raise ValueError("Function must be non-negative.")
    f_nonneg = np.maximum(f, 0.0)
    if np.sum(f_nonneg) == 0:
        raise ValueError("Function must have positive integral.")
    convolution = oaconvolve(f_nonneg, f_nonneg, mode="full")
    num_conv_points = len(convolution)
    x_points = np.linspace(-0.5, 0.5, num_conv_points + 2)
    x_intervals = np.diff(x_points)
    y_points = np.concatenate(([0], convolution, [0]))
    y1 = y_points[:-1]
    y2 = y_points[1:]
    l2_norm_squared = float(np.sum((x_intervals / 3) * (y1**2 + y1 * y2 + y2**2)))
    norm_1 = np.sum(np.abs(convolution)) / (num_conv_points + 1)
    norm_inf = np.max(np.abs(convolution))
    return float(l2_norm_squared / (norm_1 * norm_inf))

def evaluate(data: dict) -> float:
    return verify_and_compute_c2(data["values"])

# Wrapper to read from file and handle errors
if __name__ == "__main__":
    import json
    import sys

    try:
        with open("/workspace/solution.json", "r") as f:
            data = json.load(f)

        score = evaluate(data)

        # Write score to file
        with open("/workspace/score.txt", "w") as f:
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
