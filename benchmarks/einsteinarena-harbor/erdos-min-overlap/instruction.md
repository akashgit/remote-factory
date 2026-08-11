## Problem

Find a step function $h: [0, 2] \to [0, 1]$ that **minimizes** the overlap integral

$$C = \max_k \int h(x)\,(1 - h(x+k))\, dx$$

subject to the constraints $h(x) \in [0, 1]$ for all $x$ and $\int_0^2 h(x)\, dx = 1$.

## Scoring

Represent $h$ as `n_points` equally spaced samples over $[0, 2]$, with $dx = 2/n$. All values must satisfy $0 \le h[i] \le 1$. The sum is normalized to $n/2$ before scoring. The server evaluates:

$$C = \max\bigl(\text{correlate}(h,\; 1{-}h,\; \texttt{full})\bigr) \cdot dx$$

where `correlate` is computed using [numpy.correlate](https://numpy.org/doc/stable/reference/generated/numpy.correlate.html) with `mode="full"`.

Lower $C$ is better. Submit `values` — an array of floats representing the discretized function.

## Reference

Problem 6.5 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "values": // array of floats (the discretized function values)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-07**.
