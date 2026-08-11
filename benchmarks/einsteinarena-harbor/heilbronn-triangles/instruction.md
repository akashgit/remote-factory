## Problem

Place $n = 11$ points on or inside an equilateral triangle of side length 1 to **maximize** the area of the smallest triangle formed by any triple of the placed points, normalized by the bounding area:

$$\text{score} = \frac{\min_{1 \le i < j < k \le 11} \text{area}(p_i, p_j, p_k)}{\sqrt{3}/4}$$

The bounding equilateral triangle has vertices $A = (0, 0)$, $B = (1, 0)$, $C = (1/2, \sqrt{3}/2)$ and area $\sqrt{3}/4$. All points must lie on or inside this triangle.

## Scoring

Submit `points` — an array of exactly 11 points $[x, y]$. The score is the minimum triangle area formed by any triple, normalized by the bounding triangle area. Higher is better.

## Reference

Problem 6.48 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864).

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "points": // array of 11 [x, y] coordinate pairs inside the unit equilateral triangle
}
```


## Scoring Direction

**MAXIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-09**.
