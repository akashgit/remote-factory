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

def sum_pairwise_triple_products_batch(A):
    A = np.array(A, dtype=np.float64)
    M, N = A.shape
    triple_sums = np.zeros(M, dtype=A.dtype)
    if N < 2:
        return np.zeros(M, dtype=A.dtype), triple_sums
    S1 = np.sum(A, axis=1)
    S2 = np.sum(np.square(A), axis=1)
    pairwise_sums = np.square(S1) - S2
    if N >= 3:
        S3 = np.sum(np.power(A, 3), axis=1)
        triple_sums = np.power(S1, 3) - 3 * S1 * S2 + 2 * S3
    return pairwise_sums, triple_sums

def analyze_density_curve(edge_densities, triangle_densities, gap_range_min=0.0, gap_range_max=1.0):
    if edge_densities.shape != triangle_densities.shape or edge_densities.ndim != 1:
        return -1.0, -1.0
    if edge_densities.size > 0:
        sort_indices = np.argsort(edge_densities)
        sorted_x = edge_densities[sort_indices]
        sorted_y = triangle_densities[sort_indices]
        dtype = sorted_x.dtype
        full_x = np.concatenate(([np.array(0.0, dtype=dtype)], sorted_x, [np.array(1.0, dtype=dtype)]))
        full_y = np.concatenate(([np.array(0.0, dtype=dtype)], sorted_y, [np.array(1.0, dtype=dtype)]))
        unique_full_x, unique_indices_full = np.unique(full_x, return_index=True)
        if len(unique_full_x) < len(full_x):
            full_x = full_x[unique_indices_full]
            full_y = full_y[unique_indices_full]
    else:
        full_x = np.array([0.0, 1.0])
        full_y = np.array([0.0, 1.0])
    if len(full_x) < 2:
        area = 5.0 / 6.0
        max_gap_in_range = 1.0 if gap_range_min <= 0.0 < gap_range_max else 0.0
        return area, max_gap_in_range
    total_area = 0.0
    slope = 3.0
    epsilon = 1e-9
    for i in range(len(full_x) - 1):
        xi, yi = full_x[i], full_y[i]
        x_next, y_next = full_x[i + 1], full_y[i + 1]
        w = x_next - xi
        if w < epsilon:
            continue
        if yi > y_next + epsilon:
            segment_area = yi * w
        else:
            y_calc = yi + slope * w
            if y_calc <= y_next + epsilon:
                segment_area = (yi + y_calc) * w / 2.0
            else:
                delta_y = max(0.0, y_next - yi)
                if abs(slope) < epsilon:
                    segment_area = yi * w
                else:
                    w1 = delta_y / slope
                    w1 = max(0.0, min(w1, w))
                    w2 = w - w1
                    area1 = (yi + y_next) * w1 / 2.0
                    area2 = y_next * w2
                    segment_area = area1 + area2
        total_area += segment_area
    gaps = np.diff(full_x)
    indices_in_range = np.where((full_x[:-1] >= gap_range_min) & (full_x[:-1] < gap_range_max))[0]
    max_gap_in_range = float(np.max(gaps[indices_in_range])) if indices_in_range.size > 0 else 0.0
    return total_area, max_gap_in_range

def evaluate(data):
    solutions = np.array(data["weights"], dtype=np.float64)
    max_length = 20
    for i, solution in enumerate(solutions):
        assert len(solution) == max_length, f"Row {i} has length {len(solution)}, expected {max_length}"
        assert np.all(solution >= 0), f"Row {i} contains negative entries"
        assert np.sum(solution) >= 1e-7, f"Row {i} sums to near zero"
        solutions[i] = solution / np.sum(solution)
    edge_densities, triangle_densities = sum_pairwise_triple_products_batch(solutions)
    assert not np.any(np.isnan(edge_densities)), "NaN in edge densities"
    assert not np.any(np.isnan(triangle_densities)), "NaN in triangle densities"
    area, max_gap_in_range = analyze_density_curve(edge_densities, triangle_densities)
    return -(area + 10 * max_gap_in_range)

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
