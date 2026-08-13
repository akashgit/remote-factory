"""Einstein Arena verifier for circle-packing."""

import numpy as np

def evaluate(data):
    circles = np.array(data["circles"], dtype=np.float64)
    assert circles.shape == (26, 3), f"Expected (26, 3), got {circles.shape}"
    n = 26
    centers = circles[:, :2]
    radii = circles[:, 2]
    if not np.isfinite(centers).all() or not np.isfinite(radii).all():
        return -float("inf")
    if not (radii >= 0).all():
        return -float("inf")
    is_contained = (radii[:, None] <= centers) & (centers <= 1 - radii[:, None])
    if not is_contained.all():
        return -float("inf")
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))
            if radii[i] + radii[j] > dist + 1e-9:
                return -float("inf")
    return float(np.sum(radii))
