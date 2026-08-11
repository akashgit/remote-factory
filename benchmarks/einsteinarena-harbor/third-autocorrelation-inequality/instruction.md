## Problem

Find a function $f: \mathbb{R} \to \mathbb{R}$ (which **may take negative values**) that **minimizes** the constant $C$ in the third autocorrelation inequality

$$\left|\max_{-1/2 \le t \le 1/2} f \star f(t)\right| \;\ge\; C \cdot \left(\int f(x)\, dx\right)^2$$

where $f \star f(t) = \int f(t{-}x)\,f(x)\,dx$ is the autoconvolution. Unlike the first autocorrelation inequality problem, here $f$ is not restricted to be non-negative. This makes the problem harder — allowing negative values gives the optimizer more freedom to cancel out correlation peaks. 

## Scoring

Discretize $f$ on $[-1/4,\, 1/4]$ into `n_points` equally spaced values. Values may be positive or negative, but the integral $\int f$ must be non-zero. The server computes $C_3$ as:

$$dx = \frac{0.5}{n}, \qquad C = \frac{\bigl|\max\bigl(\text{convolve}(f,\, f) \cdot dx\bigr)\bigr|}{\bigl(\sum f \cdot dx\bigr)^2}$$

where `convolve` is computed using [numpy.convolve](https://numpy.org/devdocs/reference/generated/numpy.convolve.html).

Lower $C$ is better. Submit `values` — an array of floats representing the discretized function.

## Reference

Problem 6.4 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "values": // array of floats (the discretized function values, may be negative)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.
