## Problem

Place $n = 50$ points on the unit sphere $S^2 \subset \mathbb{R}^3$ to **maximize** the minimum pairwise Euclidean distance

$$d_{\min} = \min_{1 \le i < j \le n} \|\mathbf{p}_i - \mathbf{p}_j\|$$

Each submitted point is projected onto the unit sphere before scoring: $\mathbf{p}_i \leftarrow \mathbf{p}_i / \|\mathbf{p}_i\|.$

## Scoring

Submit `vectors` — an array of exactly 50 points in $\mathbb{R}^3$. Each point is normalized to the unit sphere. The score is the minimum pairwise Euclidean distance $d_{\min}$. Higher is better.

Pairwise distances below $10^{-12}$ are clamped.

## Reference

Problem 6.34 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "vectors": // array of 50 points, each [x, y, z]
}
```


## Scoring Direction

**MAXIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-08**.
