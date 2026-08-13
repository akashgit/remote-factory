"""Einstein Arena verifier for heilbronn-triangles."""

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
