## Problem

Find a non-negative function $f: \mathbb{R} \to \mathbb{R}$ that minimizes the constant $C$ in the autocorrelation inequality

$$\max_{t}\; (f \star f)(t) \;\ge\; C \cdot \left(\int f(x)\, dx\right)^2$$

where $f \star f(t) = \int f(t{-}x)\, f(x)\, dx$ is the autoconvolution. This is a classical problem in harmonic analysis — $C$ measures how "peaky" a non-negative function must be relative to its autoconvolution. 

## Scoring

Discretize $f$ on $[-1/4,\, 1/4]$ into `n_points` equally spaced values. All values must be non-negative with positive integral. The server computes $C$ as:

$$dx = \frac{0.5}{n}, \qquad C = \frac{\max\bigl(\text{convolve}(f,\, f) \cdot dx\bigr)}{\bigl(\sum f \cdot dx\bigr)^2}$$

where `convolve` is computed using [numpy.convolve](https://numpy.org/devdocs/reference/generated/numpy.convolve.html).

Lower $C$ is better. Submit `values` — an array of non-negative floats representing the discretized function.

## Reference

Problem 6.2 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "values": // array of non-negative floats (the discretized function values)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-08**.
