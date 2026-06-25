"""Repo Spec — model-readable structural map of a repository."""

from factory.spec.generate import collect_source_files, generate_spec, group_into_batches
from factory.spec.parser import parse_spec
from factory.spec.validate import validate_spec

__all__ = [
    "collect_source_files",
    "generate_spec",
    "group_into_batches",
    "parse_spec",
    "validate_spec",
]
