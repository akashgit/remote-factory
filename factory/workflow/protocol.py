"""Factory V2 Protocol — peer factories with independent evals and file-based state contracts.

A factory is a peer unit with four parts: input contract, transform, eval harness,
output contract. Factories compose by passing state summaries through files.
No parent-child relationship, no depth limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger()


class StateSummary(BaseModel):
    """File-based state handoff between peer factories."""

    model_config = ConfigDict(strict=True, extra="forbid")

    source_factory: str
    produced_files: dict[str, str] = Field(default_factory=dict)
    eval_score: float | None = None
    eval_details: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def write_summary(summary: StateSummary, path: Path) -> None:
    """Write state summary to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.model_dump(), indent=2))
    log.debug("protocol.write_summary", path=str(path), factory=summary.source_factory)


def read_summary(path: Path) -> StateSummary:
    """Read state summary from a JSON file."""
    data = json.loads(path.read_text())
    return StateSummary.model_validate(data)


def summarize_factory_output(
    factory_id: str,
    output_contract: dict[str, str],
    eval_result: dict[str, Any] | None,
    project_path: Path,
) -> StateSummary:
    """Generate a StateSummary from a completed factory's outputs.

    Reads the output files, extracts key metadata (file sizes, line counts),
    and produces a concise summary. No LLM call — purely structural.
    """
    produced_files: dict[str, str] = {}
    file_metadata: dict[str, Any] = {}

    for name, rel_path in output_contract.items():
        full_path = project_path / rel_path
        produced_files[name] = rel_path
        if full_path.exists():
            stat = full_path.stat()
            line_count = full_path.read_text().count("\n") + 1 if stat.st_size > 0 else 0
            file_metadata[name] = {
                "size_bytes": stat.st_size,
                "line_count": line_count,
                "exists": True,
            }
        else:
            file_metadata[name] = {"exists": False}

    eval_score: float | None = None
    eval_details: dict[str, Any] = {}
    if eval_result:
        eval_score = eval_result.get("score")
        eval_details = eval_result

    existing_count = sum(1 for m in file_metadata.values() if m.get("exists"))
    total_count = len(output_contract)
    summary_text = (
        f"Factory '{factory_id}' produced {existing_count}/{total_count} output files."
    )
    if eval_score is not None:
        summary_text += f" Eval score: {eval_score:.3f}."

    return StateSummary(
        source_factory=factory_id,
        produced_files=produced_files,
        eval_score=eval_score,
        eval_details=eval_details,
        summary=summary_text,
        metadata={"file_metadata": file_metadata},
    )
