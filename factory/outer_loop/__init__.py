"""Outer loop — evolutionary swarm search for workflow optimization."""

from factory.outer_loop.designer import DesignerAgent, extract_telemetry
from factory.outer_loop.engine import BudgetTracker, SwarmEngine
from factory.outer_loop.filesystem import (
    export_best_workflow,
    init_filesystem,
    load_checkpoint,
    load_config,
    save_best,
    save_checkpoint,
    save_generation,
    save_map_elites,
)
from factory.outer_loop.direct_evaluator import DirectFeatureBenchEvaluator
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
    "BudgetTracker",
    "DirectFeatureBenchEvaluator",
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
    "SwarmEngine",
    "export_best_workflow",
    "extract_telemetry",
    "init_filesystem",
    "load_checkpoint",
    "load_config",
    "save_best",
    "save_checkpoint",
    "save_generation",
    "save_map_elites",
]
