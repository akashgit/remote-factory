"""Benchmark configuration — TOML-based registry for multi-benchmark support.

Each benchmark is a .toml file with metadata, test format, instance format,
and seed workflow. The registry discovers configs from:
  1. Project-local: .factory/benchmarks/
  2. User-local: ~/.factory/benchmarks/
  3. Built-in: benchmarks/configs/ (in the factory repo)
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

import structlog

log = structlog.get_logger()

_BUILTIN_DIR = Path(__file__).resolve().parent.parent.parent / "benchmarks" / "configs"


class BenchmarkConfig(BaseModel):
    """Configuration for a single benchmark."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str = ""
    test_format: str = "pytest"
    test_command: str = ""
    test_timeout: int = 600
    instance_format: str = "directory"
    prep_command: str = ""
    seed_workflow: str = ""
    answer_extraction: str = ""
    metric_path: str = "score"
    scoring_method: str = "partial_credit"
    scoring_weights: dict[str, float] = Field(default_factory=dict)

    def to_task(self) -> object:
        """Bridge method: convert this BenchmarkConfig to a Task.

        Uses lazy import to avoid circular dependency.
        """
        from factory.task import Task

        return Task.from_legacy(
            name=self.name,
            test_command=self.test_command,
            test_format=self.test_format,
            metric_path=self.metric_path,
            instance_format=self.instance_format,
            prep_command=self.prep_command,
        )


def load_benchmark_config(name: str, project_dir: Path | None = None) -> BenchmarkConfig:
    """Load a benchmark config by name from the search path.

    Search order: project .factory/benchmarks/ → ~/.factory/benchmarks/ → built-in.
    Raises FileNotFoundError if no config found.
    """
    search_paths: list[Path] = []

    if project_dir is not None:
        search_paths.append(Path(project_dir) / ".factory" / "benchmarks")

    user_dir = Path.home() / ".factory" / "benchmarks"
    search_paths.append(user_dir)
    search_paths.append(_BUILTIN_DIR)

    for base in search_paths:
        config_path = base / f"{name}.toml"
        if config_path.exists():
            return _parse_toml(config_path, name)

    raise FileNotFoundError(
        f"No benchmark config found for {name!r}. "
        f"Searched: {[str(p) for p in search_paths]}"
    )


def list_benchmarks(project_dir: Path | None = None) -> list[BenchmarkConfig]:
    """List all available benchmark configs from the search path."""
    seen: set[str] = set()
    configs: list[BenchmarkConfig] = []

    search_paths: list[Path] = []
    if project_dir is not None:
        search_paths.append(Path(project_dir) / ".factory" / "benchmarks")
    search_paths.append(Path.home() / ".factory" / "benchmarks")
    search_paths.append(_BUILTIN_DIR)

    for base in search_paths:
        if not base.exists():
            continue
        for f in sorted(base.glob("*.toml")):
            name = f.stem
            if name in seen:
                continue
            seen.add(name)
            try:
                configs.append(_parse_toml(f, name))
            except Exception:
                log.warning("benchmark_config_parse_error", path=str(f), exc_info=True)

    return configs


def _parse_toml(path: Path, name: str) -> BenchmarkConfig:
    """Parse a TOML benchmark config file."""
    raw = tomllib.loads(path.read_text())

    meta = raw.get("meta", {})
    test = raw.get("test", {})
    instances = raw.get("instances", {})
    seed = raw.get("seed_workflow", {})
    scoring = raw.get("scoring", {})

    return BenchmarkConfig(
        name=meta.get("name", name),
        description=meta.get("description", ""),
        test_format=test.get("format", "pytest"),
        test_command=test.get("command", ""),
        test_timeout=test.get("timeout", 600),
        instance_format=instances.get("format", "directory"),
        prep_command=instances.get("prep_command", ""),
        seed_workflow=seed.get("name", ""),
        answer_extraction=test.get("answer_extraction", ""),
        metric_path=test.get("metric_path", "score"),
        scoring_method=scoring.get("method", "partial_credit"),
        scoring_weights={
            str(k): float(v) for k, v in scoring.get("weights", {}).items()
        },
    )
