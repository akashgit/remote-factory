"""Entropic adaptive beta advantage estimation.

Ported from Discover (ttt_discover/rl/train.py:103-176). Computes GRPO-style
group-relative advantages using LOO Boltzmann weights with an adaptively
solved temperature parameter beta.
"""

from __future__ import annotations

import math

import torch


def solve_beta(
    rewards: torch.Tensor,
    delta: float = math.log(2),
    beta_max: float = 1e6,
    iters: int = 60,
) -> torch.Tensor:
    """Binary search for beta where KL(q_beta || uniform) = delta.

    q_beta is the Boltzmann distribution over rewards: q ∝ exp(beta * r).
    """
    r = rewards.float()
    k = r.shape[0]

    if k < 2:
        return r.new_tensor(0.0)

    # If rewards have no variance, return beta=0 (uniform distribution is optimal)
    if r.std().item() < 1e-8:
        return r.new_tensor(0.0)

    log_k = math.log(k)

    def kl_hat(beta_scalar: float) -> float:
        b = r.new_tensor(beta_scalar)
        logits = b * (r - r.max(dim=0, keepdim=True).values)
        logq = logits - torch.logsumexp(logits, dim=0, keepdim=True)
        q = torch.exp(logq)
        kl = (q * (logq + log_k)).sum(dim=0)
        return float(kl.mean().item())

    lo, hi = 0.0, 1.0
    if kl_hat(hi) < delta:
        while hi < beta_max and kl_hat(hi) < delta:
            hi *= 2.0
        if kl_hat(hi) < delta:
            return r.new_tensor(hi)

    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if kl_hat(mid) < delta:
            lo = mid
        else:
            hi = mid

    return r.new_tensor(hi)


def entropic_advantages(
    rewards: torch.Tensor,
    beta: torch.Tensor,
    eps: float = 1e-12,
) -> torch.Tensor:
    """Compute LOO Boltzmann advantages: w_i = exp(beta*r_i) / Z_loo_i, advantage = w - 1."""
    k = rewards.shape[0]
    e = torch.exp(beta * (rewards - rewards.max(dim=0, keepdim=True).values))

    if k == 1:
        z_loo = e
    else:
        z_loo = (e.sum(dim=0, keepdim=True) - e) / (k - 1)

    w = e / (z_loo + eps)
    return w - 1.0


def compute_group_advantages(rewards: torch.Tensor) -> torch.Tensor:
    """Convenience: solve beta then compute entropic advantages for one group."""
    beta = solve_beta(rewards)
    return entropic_advantages(rewards, beta)
