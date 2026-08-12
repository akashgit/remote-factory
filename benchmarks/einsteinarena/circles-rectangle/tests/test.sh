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
import itertools

MAX_COORD = 1e6
ULP_SAFETY_FACTOR = 1e6

def evaluate(data):
    circles = np.array(data["circles"], dtype=np.float64)
    if circles.shape != (21, 3):
        return -float("inf")
    if not np.isfinite(circles).all():
        return -float("inf")
    radii = circles[:, 2]
    if not (radii > 0).all():
        return -float("inf")
    coords = circles[:, :2]
    # Reject implausible coordinate magnitudes. The problem is translation
    # invariant, so a legitimate solution never needs to live far from the
    # origin; this also rules out the float64 precision loss checked below.
    if np.abs(coords).max() > MAX_COORD:
        return -float("inf")
    # At large coordinate magnitude, the float64 gap between representable
    # numbers (ulp) can exceed the radius, so "coord +/- radius" silently
    # rounds away the radius and the bounding box below under-reports the
    # true extent. Require each radius to be resolvable well above that gap.
    ulp = np.maximum(np.abs(np.spacing(coords[:, 0])), np.abs(np.spacing(coords[:, 1])))
    if (radii < ULP_SAFETY_FACTOR * ulp).any():
        return -float("inf")
    min_x = np.min(circles[:, 0] - radii)
    max_x = np.max(circles[:, 0] + radii)
    min_y = np.min(circles[:, 1] - radii)
    max_y = np.max(circles[:, 1] + radii)
    width = max_x - min_x
    height = max_y - min_y
    if width + height > 2 + 1e-9:
        return -float("inf")
    for c1, c2 in itertools.combinations(circles, 2):
        dist = np.sqrt((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2)
        if dist < c1[2] + c2[2] - 1e-9:
            return -float("inf")
    return float(np.sum(radii))

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
