"""Einstein Arena verifier for tammes-problem."""

import numpy as np

def evaluate(data):
    vectors = np.array(data["vectors"], dtype=np.float64)
    assert vectors.shape == (50, 3), f"Expected (50, 3), got {vectors.shape}"
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1e-12
    vectors = vectors / norms
    diffs = vectors[:, None, :] - vectors[None, :, :]
    dist_sq = np.sum(diffs**2, axis=2)
    iu = np.triu_indices(50, k=1)
    dists = np.sqrt(dist_sq[iu])
    return float(np.min(dists))
