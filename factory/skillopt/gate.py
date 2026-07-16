"""Validation gate — accept or reject candidate skills based on score comparison."""
from __future__ import annotations

import structlog

from factory.skillopt.types import GateResult

log = structlog.get_logger()


def select_gate_score(hard: float, soft: float, metric: str = "hard") -> float:
    if metric == "hard":
        return hard
    if metric == "soft":
        return soft
    return (hard + soft) / 2.0


def evaluate_gate(
    candidate_skill: str,
    cand_hard: float,
    cand_soft: float,
    current_skill: str,
    current_score: float,
    best_skill: str,
    best_score: float,
    best_step: int,
    global_step: int,
    metric: str = "hard",
) -> GateResult:
    cand_score = select_gate_score(cand_hard, cand_soft, metric)

    if cand_score > best_score:
        log.info(
            "gate: accept_new_best",
            cand=round(cand_score, 4),
            prev_best=round(best_score, 4),
        )
        return GateResult(
            action="accept_new_best",
            current_skill=candidate_skill,
            current_score=cand_score,
            best_skill=candidate_skill,
            best_score=cand_score,
            best_step=global_step,
        )

    if cand_score > current_score:
        log.info(
            "gate: accept",
            cand=round(cand_score, 4),
            current=round(current_score, 4),
        )
        return GateResult(
            action="accept",
            current_skill=candidate_skill,
            current_score=cand_score,
            best_skill=best_skill,
            best_score=best_score,
            best_step=best_step,
        )

    log.info(
        "gate: reject",
        cand=round(cand_score, 4),
        current=round(current_score, 4),
    )
    return GateResult(
        action="reject",
        current_skill=current_skill,
        current_score=current_score,
        best_skill=best_skill,
        best_score=best_score,
        best_step=best_step,
    )
