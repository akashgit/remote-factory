## Problem

Find a set $B$ of non-negative integers such that every positive integer up to some value $v$ appears as a difference $b_i - b_j$ for some $b_i > b_j \in B$. **Minimize** the ratio $|B|^2 / v$.

$$\text{score} = \frac{|B|^2}{v}$$

where $v$ is the largest integer $\ge 1$ such that every integer in $\{1, \ldots, v\}$ is representable as a positive difference within $B$. Lower is better.

## Scoring

Submit `set` — a list of non-negative integers (at most 2000 unique elements, 0 must be included or will be added). The score is $|B|^2 / v$ where $B$ is the deduplicated, sorted set and $v$ is the largest contiguously covered value. Lower is better.

## Reference

Problem 6.7 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864).

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "set": // list of non-negative integers (up to 2000 elements)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-09**.
