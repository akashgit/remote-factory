## Problem

Find a non-negative function $f: \mathbb{R} \to \mathbb{R}$ that **maximizes** the constant $C$ in the second autocorrelation inequality

$$\|f \star f\|_2^2 \;\le\; C \;\|f \star f\|_1 \;\|f \star f\|_\infty$$

where $f \star f(t) = \int f(t{-}x)\,f(x)\,dx$ is the autoconvolution. The constant $C$ measures the tightest ratio between the $L^2$ norm squared of the autoconvolution and the product of its $L^1$ and $L^\infty$ norms.

## Scoring

Discretize $f$ as `n_points` values (the number of discretization points is your choice, up to 2,000,000). All values must be non-negative. The server computes $C$ as:

$$C = \frac{\|f \star f\|_2^2}{\|f \star f\|_1 \cdot \|f \star f\|_\infty}$$

using piecewise-linear integration for the $L^2$ norm and discrete approximations for $L^1$ and $L^\infty$. The autoconvolution $f \star f$ is computed using [scipy.signal.oaconvolve](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.oaconvolve.html) (overlap-add FFT, equivalent to direct convolution to machine precision). Higher $C$ is better. Submit `values` — an array of non-negative floats.

## Reference

Problem 6.3 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "values": // array of non-negative floats (the discretized function values)
}
```


## Scoring Direction

**MAXIMIZE**

The verifier will evaluate your solution and return a numerical score.


## Minimum Improvement

To claim a better score on the leaderboard, your solution must improve upon the current best by at least **1e-05**.
