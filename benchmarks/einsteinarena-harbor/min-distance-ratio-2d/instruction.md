## Problem

Place $n = 16$ points in the 2-dimensional plane so as to **minimize** the squared ratio between the maximum and minimum pairwise Euclidean distances:

$$R = \left(\frac{\max_{i < j} \|p_i - p_j\|}{\min_{i < j} \|p_i - p_j\|}\right)^2$$

This is a classical problem in discrete geometry related to point packing and optimal configurations. The squared ratio convention follows [Erich Friedman's compendium](https://erich-friedman.github.io/packing/maxmin/).

## Scoring

Submit exactly 16 points as a list of $[x, y]$ coordinate pairs. All points must be distinct (minimum pairwise distance $> 10^{-12}$). The server computes all $\binom{16}{2} = 120$ pairwise Euclidean distances, then returns:

$$R = \left(\frac{d_{\max}}{d_{\min}}\right)^2$$

Lower $R$ is better. Submit `vectors` — an array of 16 coordinate pairs `[[x1, y1], [x2, y2], ...]`.

## Reference

Problem 6.50 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "vectors": // array of 16 [x, y] coordinate pairs
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-07**.
