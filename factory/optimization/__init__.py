"""Unified optimization loop — pluggable Executor, Evaluator, Mutator protocols."""

from factory.optimization.analyzer import StepAnalyzer as StepAnalyzer
from factory.optimization.gate import evaluate_gate as evaluate_gate
from factory.optimization.loop import OptimizationLoop as OptimizationLoop
from factory.optimization.loop import TrainResult as TrainResult
from factory.optimization.protocols import Evaluator as Evaluator
from factory.optimization.protocols import Executor as Executor
from factory.optimization.protocols import Mutator as Mutator
from factory.optimization.surface import Surface as Surface
from factory.optimization.types import ExecutionResult as ExecutionResult
from factory.optimization.types import GateResult as GateResult
from factory.optimization.types import GraphMutation as GraphMutation
from factory.optimization.types import LoopConfig as LoopConfig
from factory.optimization.types import Patch as Patch
from factory.optimization.types import SlotEdit as SlotEdit
from factory.optimization.types import StepRecord as StepRecord

__all__ = [
    "Evaluator",
    "ExecutionResult",
    "Executor",
    "GateResult",
    "GraphMutation",
    "LoopConfig",
    "Mutator",
    "OptimizationLoop",
    "Patch",
    "SlotEdit",
    "StepAnalyzer",
    "StepRecord",
    "Surface",
    "TrainResult",
    "evaluate_gate",
]
