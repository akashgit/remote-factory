## Problem

Let $\pi(x)$ denote the number of primes less than or equal to $x$, and define

$$C^- := \liminf_{x \to \infty} \frac{\pi(x)}{x / \log x}, \qquad C^+ := \limsup_{x \to \infty} \frac{\pi(x)}{x / \log x}$$

**What are $C^-$ and $C^+$?**

The answer — $C^- = C^+ = 1$ — is the Prime Number Theorem. Your task is to construct a *certificate* of this fact: a partial function $f$ defined on a finite set of positive integers that makes the constructive proof as tight as possible.

## Scoring

Submit a partial function $f$ as a dictionary mapping positive integer keys (as strings) to real values. The server:

1. Clips all values to $[-10, 10]$
2. Adjusts $f(1)$ so that $\sum_k f(k)/k = 0$ (normalization)
3. Draws $10^7$ random samples $x \sim \mathrm{Uniform}(1,\, 10 \cdot \max_{f(k) \neq 0} (k) )$ and checks $\sum_k f(k)\lfloor x/k \rfloor \le 1$ — if any sample fails, the solution is invalid
4. Returns $S(f) = -\sum_k f(k) \log(k) / k$

Higher $S(f)$ is better. The theoretical maximum is $S = 1$, achieved by $f = \mu$ (the Möbius function). Submit `partial_function` — a JSON object with positive integer keys (as strings) and float values.

**Note:** The constraint check (step 3) uses Monte Carlo sampling with a fixed random seed. A passing score does not constitute a proof — it is a numerical certificate. High-scoring solutions should be verified analytically to confirm the constraint $\sum_k f(k)\lfloor x/k \rfloor \le 1$ holds for all $x \ge 1$.

## Reference

Problem 6.27 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "partial_function": // object mapping positive integer keys (as strings) to float values
}
```


## Scoring Direction

**MAXIMIZE**

The verifier will evaluate your solution and return a numerical score.
