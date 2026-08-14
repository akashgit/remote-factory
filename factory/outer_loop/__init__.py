"""Outer loop — evolutionary swarm search for workflow optimization."""

from factory.outer_loop.designer import DesignerAgent, extract_telemetry
from factory.outer_loop.models import (
    AuditResult,
    EvalResult,
    GenerationSummary,
    HyperparameterRecord,
    Individual,
    MutationRecord,
    MutationType,
    OuterLoopResult,
    OuterLoopState,
    SwarmConfig,
)

__all__ = [
    "AuditResult",
    "DesignerAgent",
    "EvalResult",
    "GenerationSummary",
    "HyperparameterRecord",
    "Individual",
    "MutationRecord",
    "MutationType",
    "OuterLoopResult",
    "OuterLoopState",
    "SwarmConfig",
    "extract_telemetry",
]
