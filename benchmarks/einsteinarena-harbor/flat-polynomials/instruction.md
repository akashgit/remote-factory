## Problem

Choose $\pm 1$ coefficients $c_0, c_1, \ldots, c_{69}$ for a degree-69 polynomial

$$g(z) = c_0 z^{69} + c_1 z^{68} + \cdots + c_{69}$$

to **minimize** the $C^+$ score

$$C^+ = \frac{\max_{|z|=1} |g(z)|}{\sqrt{71}}$$

This measures how "flat" the polynomial is on the unit circle. 

## Scoring

Submit `coefficients` — an array of exactly 70 integers, each $+1$ or $-1$. The array is passed to `np.poly1d`, so the first element multiplies $z^{69}$ and the last is the constant term. The polynomial is evaluated at $10^6$ equally spaced points on the unit circle. The score is the ratio of the maximum modulus to $\sqrt{71}$. Lower is better.

## Reference

Problem 6.28 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "coefficients": // array of 70 values, each +1 or -1
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.
