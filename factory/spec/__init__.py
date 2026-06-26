"""GRAPH-SPEC — model-readable structural map of a repository."""

from factory.spec.generate import collect_source_files, generate_spec, group_into_batches
from factory.spec.impact import get_impact
from factory.spec.parser import parse_spec
from factory.spec.update import DiffScope, scope_diff, update_spec
from factory.spec.validate import validate_spec

__all__ = [
    "DiffScope",
    "collect_source_files",
    "generate_spec",
    "get_impact",
    "group_into_batches",
    "parse_spec",
    "scope_diff",
    "update_spec",
    "validate_spec",
]
