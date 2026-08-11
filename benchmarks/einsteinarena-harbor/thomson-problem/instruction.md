## Problem

Place $n = 282$ points on the unit sphere $S^2 \subset \mathbb{R}^3$ to **minimize** the Coulomb energy

$$E = \sum_{1 \le i < j \le n} \frac{1}{\|\mathbf{p}_i - \mathbf{p}_j\|}$$

Each submitted point is projected onto the unit sphere before scoring: $\mathbf{p}_i \leftarrow \mathbf{p}_i / \|\mathbf{p}_i\|.$

## Scoring

Submit `vectors` — an array of exactly 282 points in $\mathbb{R}^3$. Each point is normalized to the unit sphere. The score is the total Coulomb energy $E$ (sum of reciprocal pairwise Euclidean distances). Lower is better.

Pairwise distances below $10^{-12}$ are clamped to avoid division by zero.

## Reference

Problem 6.33 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "vectors": // array of 282 points, each [x, y, z]
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.
