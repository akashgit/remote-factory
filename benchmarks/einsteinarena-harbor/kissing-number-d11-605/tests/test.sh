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
import itertools
from decimal import Decimal, getcontext

getcontext().prec = 80

ZERO = Decimal(0)
TWO = Decimal(2)
FOUR = Decimal(4)


def _to_dec(x):
    return Decimal(str(x))


def _exact_check(vectors):
    d = len(vectors[0])
    dec_vecs = [[_to_dec(x) for x in vec] for vec in vectors]

    squared_norms = [sum(x * x for x in vec) for vec in dec_vecs]
    if min(squared_norms) == ZERO:
        return False
    max_sq_norm = max(squared_norms)

    min_sq_dist = None
    for p, q in itertools.combinations(dec_vecs, 2):
        sq_dist = sum((a - b) ** 2 for a, b in zip(p, q))
        if min_sq_dist is None or sq_dist < min_sq_dist:
            min_sq_dist = sq_dist

    return min_sq_dist >= max_sq_norm


def _overlap_loss(vectors):
    d = len(vectors[0])
    scaled = []
    for vec in vectors:
        norm_sq = sum((_to_dec(x) ** 2 for x in vec), ZERO)
        if norm_sq == ZERO:
            raise ValueError("All vectors must be non-zero")
        norm = norm_sq.sqrt()
        scaled.append([(_to_dec(x) * TWO) / norm for x in vec])

    n = len(scaled)
    total = ZERO
    for i in range(n):
        for j in range(i + 1, n):
            sq = sum(((scaled[i][k] - scaled[j][k]) ** 2 for k in range(d)), ZERO)
            if sq < FOUR:
                total += (TWO - sq.sqrt())
    return float(total)


def evaluate(data: dict) -> float:
    vectors = data["vectors"]
    if len(vectors) != 605 or len(vectors[0]) != 11:
        raise ValueError(f"Expected shape (605, 11), got ({len(vectors)}, {len(vectors[0])})")
    if _exact_check(vectors):
        return 0.0
    return _overlap_loss(vectors)

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
