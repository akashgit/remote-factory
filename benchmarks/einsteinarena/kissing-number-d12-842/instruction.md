## Problem

The kissing number problem asks: how many non-overlapping unit spheres can simultaneously touch a central unit sphere in $d$ dimensions?

For $d = 12$, the best known lower bound is **841** ([Takhanov et al., 2026](https://arxiv.org/pdf/2606.18984)), improving on the previous record of **840** achieved by the Coxeter-Todd lattice $K_{12}$ ([Leech and Sloane, 1971](https://doi.org/10.4153/CJM-1971-081-3)). The best known upper bound is **1355** ([de Laat and Leijenhorst, 2024](https://doi.org/10.1007/s12532-024-00264-w)).

**Your goal:** Find a configuration of **842** unit spheres that all touch a central unit sphere in 12 dimensions, with no overlaps. This would establish a new world record lower bound for the kissing number in dimension 12.

## Setup

Submit 842 non-zero vectors in $\mathbb{R}^{12}$. Each vector $x_i$ defines a direction — the server normalizes it and places a unit sphere at $2x_i / \|x_i\|$ (distance 2 from the origin, i.e. touching the central unit sphere).

For each pair of sphere centers, the overlap penalty is:

$$\text{loss} = \sum_{i < j} \max(0,\; 2 - \|c_i - c_j\|)$$

where $c_i = 2x_i / \|x_i\|$.

## Scoring

Lower is better. Any score $> 0$ means some spheres still overlap.

A score of exactly **0** means a valid kissing configuration — proof that the kissing number in dimension 12 is at least 842. To achieve score 0, submit integer-valued vectors: the verifier will use exact integer arithmetic to confirm that $\min_{i < j} \|v_i - v_j\|^2 \geq \max_i \|v_i\|^2$, which guarantees non-overlap without floating-point error.

Submit `vectors` — an array of 842 vectors in $\mathbb{R}^{12}$, each a list of 12 numbers (integers or floats).

## Reference

[Kissing numbers table](https://cohn.mit.edu/kissing-numbers/) by Henry Cohn (MIT). Current lower bound reference: Takhanov et al. (2026), [A Kissing Configuration in 12 Dimensions with 841 Spheres](https://arxiv.org/pdf/2606.18984).

## Solution Format

Submit a JSON file named `solution.json` with the following structure:

```json
{
  "vectors": // array of 842 vectors in R^12 (each a list of 12 numbers)
}
```


## Scoring Direction

**MINIMIZE**

The verifier will evaluate your solution and return a numerical score.
