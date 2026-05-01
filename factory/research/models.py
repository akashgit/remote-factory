"""Research-specific data classes for run results and errors."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class RunStatus(str, Enum):
    """Possible outcomes of a research run."""

    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class RunResult(BaseModel):
    """Result of executing a research run command."""

    model_config = ConfigDict(strict=True, extra="forbid")

    status: RunStatus
    metric_value: float
    duration_seconds: float
    artifacts_path: Path
    stdout: str
    stderr: str


class ResultParseError(Exception):
    """Raised when a result file cannot be parsed to extract the target metric."""
