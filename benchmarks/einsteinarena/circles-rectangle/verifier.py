"""Einstein Arena verifier for circles-rectangle."""

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
