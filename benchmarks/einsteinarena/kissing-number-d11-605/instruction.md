## Problem

The kissing number problem asks: how many non-overlapping unit spheres can simultaneously touch a central unit sphere in $d$ dimensions?

For $d = 11$, as of June 2026, the best known lower bound is **604** ([EinsteinArena / Bianchi et al., 2026](https://arxiv.org/abs/2606.10402)).

**Your goal:** Find a configuration of **605** unit spheres that all touch a central unit sphere in 11 dimensions, with no overlaps. This would establish a new lower bound.

## Setup

Submit 605 non-zero vectors in $\mathbb{R}^{11}$. Each vector $x_i$ defines a direction — the server normalizes it and places a unit sphere at $2x_i / \|x_i\|$ (distance 2 from the origin, i.e. touching the central unit sphere).

For each pair of sphere centers at distance $d < 2$, the spheres overlap. The penalty is:

$$\text{loss} = \sum_{i < j} \max(0,\; 2 - \|c_i - c_j\|)$$

where $c_i = 2x_i / \|x_i\|$.

## Scoring

Lower is better. Any score $> 0$ means some spheres still overlap.

A score of exactly **0** means a valid kissing configuration — proof that the kissing number in dimension 11 is at least 605. To achieve score 0, submit integer-valued vectors: the verifier will use exact integer arithmetic to confirm that $\min_{i < j} \|v_i - v_j\|^2 \geq \max_i \|v_i\|^2$, which guarantees non-overlap without floating-point error.

Submit `vectors` — an array of 605 vectors in $\mathbb{R}^{11}$, each a list of 11 numbers (floats or integers).

## Reference

Problem 6.8 of [Mathematical exploration and discovery at scale](https://arxiv.org/abs/2511.02864)

[EinsteinArena](https://arxiv.org/abs/2606.10402)

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "vectors": // array of 605 vectors in R^11 (each a list of 11 float64 values or high-precision decimal strings with up to 80 significant digits)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.
