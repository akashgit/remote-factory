"""Result parsing — extracts a scalar metric from structured result files."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.research.models import ResultParseError

log = structlog.get_logger()


def parse_result(result_path: Path, result_parser: str, metric: str) -> float:
    """Parse a result file and extract the target metric as a float.

    Supports dotted paths (e.g. ``results.accuracy``) and slash-ratio paths
    (e.g. ``resolved/total`` which computes numerator / denominator).
    """
    if result_parser != "json":
        raise ResultParseError(f"unsupported parser: {result_parser}")

    if not result_path.exists():
        raise ResultParseError(f"result file not found: {result_path}")

    try:
        data = json.loads(result_path.read_text())
    except json.JSONDecodeError as exc:
        raise ResultParseError(f"invalid JSON in {result_path}: {exc}") from exc

    log.debug("parsing_result", path=str(result_path), metric=metric)

    if "/" in metric:
        return _parse_ratio(data, metric)
    return _navigate(data, metric)


def _navigate(data: dict, key_path: str) -> float:
    """Walk a dotted key path (e.g. ``results.accuracy``) and return the leaf as float."""
    parts = key_path.split(".")
    current: object = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise ResultParseError(f"key path '{key_path}' not found in result data")
        current = current[part]

    try:
        return float(current)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ResultParseError(
            f"value at '{key_path}' is not numeric: {current!r}"
        ) from exc


def _parse_ratio(data: dict, metric: str) -> float:
    """Parse a slash-ratio path like ``resolved/total`` → numerator / denominator."""
    parts = metric.split("/")
    if len(parts) != 2:
        raise ResultParseError(f"ratio metric must have exactly two parts: {metric}")

    numerator_key, denominator_key = parts
    numerator = _navigate(data, numerator_key)
    denominator = _navigate(data, denominator_key)

    if denominator == 0:
        raise ResultParseError(f"denominator '{denominator_key}' is zero")

    return numerator / denominator
