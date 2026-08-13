"""Einstein Arena verifier for thomson-problem."""

import numpy as np

def evaluate(data):
    vectors = np.array(data["vectors"], dtype=np.float64)
    assert vectors.shape == (282, 3), f"Expected (282, 3), got {vectors.shape}"
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1e-12
    vectors = vectors / norms
    diffs = vectors[:, None, :] - vectors[None, :, :]
    dist_sq = np.sum(diffs**2, axis=2)
    iu = np.triu_indices(282, k=1)
    dists = np.sqrt(dist_sq[iu])
    dists[dists < 1e-12] = 1e-12
    return float(np.sum(1.0 / dists))
