"""Accept/reject gate for optimization steps."""

from __future__ import annotations

from factory.optimization.types import GateResult


def evaluate_gate(
    candidate_score: float,
    current_score: float,
    best_score: float,
    best_step: int,
    global_step: int,
) -> GateResult:
    """Decide whether to accept a candidate based on score comparison."""
    if candidate_score > current_score:
        return GateResult(
            accepted=True,
            reason=f"Candidate {candidate_score:.4f} > current {current_score:.4f}",
            candidate_score=candidate_score,
            current_score=current_score,
            best_score=max(best_score, candidate_score),
        )
    return GateResult(
        accepted=False,
        reason=f"Candidate {candidate_score:.4f} <= current {current_score:.4f}",
        candidate_score=candidate_score,
        current_score=current_score,
        best_score=best_score,
    )
