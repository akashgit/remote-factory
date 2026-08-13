"""Einstein Arena verifier for kissing-number-d12-842."""

import itertools
from decimal import Decimal, getcontext

getcontext().prec = 30

ZERO = Decimal(0)
TWO = Decimal(2)
FOUR = Decimal(4)


def _to_dec(x):
    return Decimal(str(x))


def _exact_check(vectors):
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
    n, d = 842, 12

    if len(vectors) != n:
        raise ValueError(f"Expected {n} vectors, got {len(vectors)}")
    for v in vectors:
        if len(v) != d:
            raise ValueError(f"Each vector must have {d} components, got {len(v)}")

    if _exact_check(vectors):
        return 0.0
    return _overlap_loss(vectors)
