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
import itertools

def evaluate(data):
    points = np.array(data["points"], dtype=np.float64)
    if points.shape != (11, 2):
        return -float("inf")
    if not np.isfinite(points).all():
        return -float("inf")
    sq3 = np.sqrt(3)
    for x, y in points:
        if y < -1e-9:
            return -float("inf")
        if sq3 * x + y > sq3 + 1e-9:
            return -float("inf")
        if y > sq3 * x + 1e-9:
            return -float("inf")
    def tri_area(p1, p2, p3):
        return abs(p1[0]*(p2[1]-p3[1]) + p2[0]*(p3[1]-p1[1]) + p3[0]*(p1[1]-p2[1])) / 2
    a = np.array([0.0, 0.0])
    b = np.array([1.0, 0.0])
    c = np.array([0.5, np.sqrt(3)/2])
    bounding = tri_area(a, b, c)
    min_area = min(
        tri_area(points[i], points[j], points[k])
        for i, j, k in itertools.combinations(range(11), 3)
    )
    return float(min_area / bounding)

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
