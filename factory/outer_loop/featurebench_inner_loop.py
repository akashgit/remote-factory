"""Backward-compat shim. Canonical: factory.outer_loop.benchmark_inner_loop."""
from factory.outer_loop.benchmark_inner_loop import BenchmarkInnerLoop

FeatureBenchInnerLoop = BenchmarkInnerLoop

__all__ = ["FeatureBenchInnerLoop"]
