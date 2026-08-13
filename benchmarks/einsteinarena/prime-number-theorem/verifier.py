"""Einstein Arena verifier for prime-number-theorem."""

import numpy as np

NUM_SAMPLES = 10_000_000
_TARGET_BATCH_BYTES = 40 * 1024 * 1024

def evaluate(solution: dict) -> float:
    raw = solution["partial_function"]
    if len(raw) > 2000:
        raise ValueError("partial_function must have at most 2000 keys")
    pf = {int(k): np.clip(float(v), -10, 10) for k, v in raw.items()}
    total = sum(v / k for k, v in pf.items())
    pf[1] = pf.get(1, 0.0) - total
    keys = np.array(list(pf.keys()), dtype=np.float64)
    values = np.array(list(pf.values()), dtype=np.float64)
    upper_bound = 10.0 * float(np.max(keys))
    batch_size = max(1, _TARGET_BATCH_BYTES // (len(keys) * 8))
    rng = np.random.RandomState(42)
    remaining = NUM_SAMPLES
    while remaining > 0:
        n = min(batch_size, remaining)
        x = rng.uniform(1, upper_bound, size=n)
        floors = np.floor(x[:, None] / keys[None, :])
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            x_sums = floors @ values
        if np.any(x_sums > 1.0001):
            return float(-np.inf)
        remaining -= n
    return float(-np.sum(values * np.log(keys) / keys))
