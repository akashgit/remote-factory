"""Experiment filesystem store — manages .factory/ directory structure."""

import csv
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal

import structlog

from factory.models import (
    CompositeScore,
    CostBudgetConfig,
    EvalProfile,
    EvalWeights,
    ExperimentRecord,
    FactoryConfig,
    HypothesisBudget,
    ProjectEvalDimension,
    ResearchTarget,
)

log = structlog.get_logger()


TSV_COLUMNS = [
    "id", "timestamp", "hypothesis", "change_summary", "issue_number",
    "pr_number", "score_before", "score_after", "delta", "verdict",
    "cost_usd", "notes", "research_citations",
]


def _parse_kv_list(
    items: str | list[str] | float,
    value_type: type = str,
) -> dict[str, object]:
    """Parse a list of 'key: value' strings into a dict, casting values to value_type."""
    if not isinstance(items, list):
        return {}
    result: dict[str, object] = {}
    for item in items:
        s = str(item)
        if ":" in s:
            key, val = s.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            try:
                result[key] = value_type(val.strip())  # type: ignore[call-arg]
            except (ValueError, TypeError):
                pass
    return result


def _parse_project_eval(items: str | list[str] | float) -> list[ProjectEvalDimension]:
    """Parse project eval dimension entries from factory.md.

    Each list item starts with 'name: X' and may have continuation lines
    with key: value pairs (command, parse, weight, timeout, description).
    """
    if not isinstance(items, list):
        return []
    dims: list[ProjectEvalDimension] = []
    for item in items:
        lines = str(item).split("\n")
        fields: dict[str, str] = {}
        for line in lines:
            if ":" in line:
                key, val = line.split(":", 1)
                fields[key.strip()] = val.strip()
        name = fields.get("name", "")
        command = fields.get("command", "")
        if not name or not command:
            continue
        dims.append(ProjectEvalDimension(
            name=name,
            command=command,
            parse=fields.get("parse", "json"),  # type: ignore[arg-type]
            weight=float(fields.get("weight", "1.0")),
            timeout=float(fields.get("timeout", "300")),
            description=fields.get("description", ""),
        ))
    return dims


def _parse_research_target(items: str | list[str] | float) -> ResearchTarget | None:
    """Parse research target key-value block from factory.md."""
    kv = _parse_kv_list(items, str)
    if not kv:
        return None
    objective = str(kv.get("objective", ""))
    metric = str(kv.get("metric", ""))
    run_command = str(kv.get("run_command", ""))
    result_path = str(kv.get("result_path", ""))
    if not objective or not metric or not run_command or not result_path:
        log.warning("research_target_incomplete", keys=list(kv.keys()))
        return None
    rt = ResearchTarget(
        objective=objective,
        metric=metric,
        target=float(str(kv.get("target", "0.0"))),
        run_command=run_command,
        result_path=result_path,
        timeout=int(float(str(kv.get("timeout", "3600")))),
    )
    log.debug("research_target_parsed", metric=metric, target=rt.target)
    return rt


def _parse_cost_budget(items: str | list[str] | float) -> CostBudgetConfig | None:
    """Parse cost budget key-value block from factory.md."""
    kv = _parse_kv_list(items, float)
    if not kv:
        return None
    budget = CostBudgetConfig(
        max_per_cycle=float(str(kv["max_per_cycle"])) if "max_per_cycle" in kv else None,
        max_total=float(str(kv["max_total"])) if "max_total" in kv else None,
    )
    log.debug("cost_budget_parsed", max_per_cycle=budget.max_per_cycle, max_total=budget.max_total)
    return budget


class ExperimentStore:
    """Manages the .factory/ directory for a project."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.factory_dir = project_path / ".factory"

    async def init(self, config: FactoryConfig) -> None:
        """Create .factory/ structure with config.json, results.tsv, experiments/, strategy/."""
        log.info("store_init", project=str(self.project_path), goal=config.goal)
        self.factory_dir.mkdir(exist_ok=True)
        (self.factory_dir / "experiments").mkdir(exist_ok=True)
        (self.factory_dir / "strategy").mkdir(exist_ok=True)
        (self.factory_dir / "agents").mkdir(exist_ok=True)
        (self.factory_dir / "reviews").mkdir(exist_ok=True)

        (self.factory_dir / "config.json").write_text(
            json.dumps(config.model_dump(), indent=2) + "\n"
        )

        tsv_path = self.factory_dir / "results.tsv"
        if not tsv_path.exists():
            buf = io.StringIO()
            writer = csv.writer(buf, dialect="excel-tab")
            writer.writerow(TSV_COLUMNS)
            tsv_path.write_text(buf.getvalue())
        log.debug("store_init_complete", factory_dir=str(self.factory_dir))

    async def reparse_config(self) -> FactoryConfig:
        """Re-read factory.md from project root, regenerate config.json."""
        log.debug("reparse_config_start", project=str(self.project_path))
        factory_md = self.project_path / "factory.md"
        text = factory_md.read_text()

        parsed: dict[str, str | list[str] | float] = {}
        current_section: str | None = None
        list_buffer: list[str] = []
        in_code_block = False

        # Section name mapping: template heading → config key
        section_map: dict[str, str] = {
            "command": "eval_command",
            "threshold": "eval_threshold",
            "modifiable": "scope",
            "read_only": "read_only",
        }

        def _flush_list() -> None:
            if current_section and list_buffer:
                parsed[current_section] = list(list_buffer)
                list_buffer.clear()

        for line in text.splitlines():
            stripped = line.strip()

            # Skip HTML comments
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue

            # Track code fences
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                # Content inside code blocks is treated as a value
                if stripped and current_section:
                    parsed[current_section] = stripped
                continue

            if stripped.startswith("#"):
                _flush_list()
                level = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped.lstrip("#").strip().lower().replace(" ", "_")
                mapped = section_map.get(heading, heading)
                if level <= 2:
                    current_section = mapped
                else:
                    current_section = mapped
            elif stripped.startswith("- ") and current_section:
                list_buffer.append(stripped[2:].strip())
            elif stripped and current_section and list_buffer and line.startswith("  "):
                # Continuation line (indented, follows a list item)
                list_buffer[-1] += "\n" + stripped
            elif stripped and current_section and not list_buffer:
                if current_section == "eval_threshold":
                    parsed[current_section] = float(stripped)
                else:
                    parsed[current_section] = stripped
        _flush_list()

        budget_kwargs: dict[str, object] = _parse_kv_list(parsed.get("hypothesis_budget", []), int)
        weights_kwargs: dict[str, object] = _parse_kv_list(parsed.get("eval_weights", []), float)
        project_eval_dims = _parse_project_eval(parsed.get("project_eval", []))

        smoke_test_raw = parsed.get("smoke_test", "")
        smoke_test = str(smoke_test_raw).strip() if smoke_test_raw else ""

        research_target = _parse_research_target(parsed.get("research_target", []))
        cost_budget = _parse_cost_budget(parsed.get("cost_budget", []))

        mutable_raw = parsed.get("mutable_surfaces", [])
        mutable_surfaces = list(mutable_raw) if isinstance(mutable_raw, list) else []
        fixed_raw = parsed.get("fixed_surfaces", [])
        fixed_surfaces = list(fixed_raw) if isinstance(fixed_raw, list) else []
        rc_raw = parsed.get("research_constraints", [])
        research_constraints = list(rc_raw) if isinstance(rc_raw, list) else []

        config = FactoryConfig(
            goal=str(parsed.get("goal", "")),
            scope=list(parsed.get("scope", [])),  # type: ignore[arg-type]
            guards=list(parsed.get("guards", [])),  # type: ignore[arg-type]
            eval_command=str(parsed.get("eval_command", "")),
            eval_threshold=float(parsed.get("eval_threshold", 0.0)),  # type: ignore[arg-type]
            constraints=list(parsed.get("constraints", [])),  # type: ignore[arg-type]
            hypothesis_budget=HypothesisBudget(**budget_kwargs) if budget_kwargs else HypothesisBudget(),  # type: ignore[arg-type]
            target_branch=str(parsed.get("target_branch", "main")),
            smoke_test=smoke_test,
            project_eval=project_eval_dims,
            eval_weights=EvalWeights(**weights_kwargs) if weights_kwargs else EvalWeights(),  # type: ignore[arg-type]
            research_target=research_target,
            mutable_surfaces=mutable_surfaces,
            fixed_surfaces=fixed_surfaces,
            research_constraints=research_constraints,
            cost_budget=cost_budget,
        )

        (self.factory_dir / "config.json").write_text(
            json.dumps(config.model_dump(), indent=2) + "\n"
        )
        log.info("reparse_config_complete", goal=config.goal, scope_count=len(config.scope))
        return config

    async def next_id(self) -> int:
        """Return max existing experiment ID + 1, or 1 if none exist."""
        experiments_dir = self.factory_dir / "experiments"
        if not experiments_dir.exists():
            log.debug("next_id_no_experiments_dir")
            return 1
        ids = [
            int(d.name)
            for d in experiments_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]
        next_val = max(ids) + 1 if ids else 1
        log.debug("next_id_computed", next_id=next_val, existing_count=len(ids))
        return next_val

    async def begin(self, hypothesis: str) -> int:
        """Create experiments/NNN/hypothesis.md, return the experiment ID.

        Idempotent: if the next experiment dir already exists (e.g. from a
        previous interrupted run), return its ID without crashing.
        """
        exp_id = await self.next_id()
        log.info("experiment_begin", exp_id=exp_id, hypothesis=hypothesis[:80])
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        exp_dir.mkdir(parents=True, exist_ok=True)
        hyp_path = exp_dir / "hypothesis.md"
        if not hyp_path.exists():
            hyp_path.write_text(hypothesis)
        return exp_id

    async def save_eval(
        self,
        exp_id: int,
        phase: Literal["before", "after"],
        score: CompositeScore,
    ) -> None:
        """Write eval_before.json or eval_after.json into the experiment dir."""
        log.debug("save_eval", exp_id=exp_id, phase=phase, score=score.total)
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        filename = f"eval_{phase}.json"
        (exp_dir / filename).write_text(
            json.dumps(score.model_dump(), indent=2, default=str) + "\n"
        )

    async def save_diff(self, exp_id: int) -> None:
        """Capture git diff HEAD~1 into changes.diff."""
        log.debug("save_diff", exp_id=exp_id)
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        result = subprocess.run(
            ["git", "diff", "HEAD~1"],
            cwd=self.project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        (exp_dir / "changes.diff").write_text(result.stdout)

    async def finalize(self, exp_id: int, record: ExperimentRecord) -> None:
        """Write verdict.json and append row to results.tsv.

        Creates the experiment directory if it is missing (e.g. after git clean).
        Computes delta from score_before/score_after if not already set.
        """
        delta = record.delta
        if delta is None and record.score_before is not None and record.score_after is not None:
            delta = round(record.score_after - record.score_before, 6)

        log.info(
            "experiment_finalize",
            exp_id=exp_id,
            verdict=record.verdict,
            delta=delta,
        )
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        record_dump = record.model_dump()
        record_dump["delta"] = delta
        (exp_dir / "verdict.json").write_text(
            json.dumps(record_dump, indent=2, default=str) + "\n"
        )

        tsv_path = self.factory_dir / "results.tsv"
        with open(tsv_path, "a", newline="") as f:
            writer = csv.writer(f, dialect="excel-tab")
            writer.writerow([
                record.id,
                record.timestamp.isoformat(),
                record.hypothesis,
                record.change_summary,
                record.issue_number if record.issue_number is not None else "",
                record.pr_number if record.pr_number is not None else "",
                record.score_before if record.score_before is not None else "",
                record.score_after if record.score_after is not None else "",
                delta if delta is not None else "",
                record.verdict,
                record.cost_usd if record.cost_usd is not None else "",
                record.notes,
                "|".join(record.research_citations) if record.research_citations else "",
            ])

    async def load_history(self) -> list[ExperimentRecord]:
        """Parse results.tsv into a list of ExperimentRecords."""
        tsv_path = self.factory_dir / "results.tsv"
        if not tsv_path.exists():
            log.debug("load_history_no_tsv", path=str(tsv_path))
            return []

        records: list[ExperimentRecord] = []
        valid_verdicts = {"keep", "revert", "error"}
        with open(tsv_path, newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            for row in reader:
                def _safe_int(val: str) -> int | None:
                    if not val or val in ("-", "n/a"):
                        return None
                    try:
                        return int(val)
                    except ValueError:
                        return None

                def _safe_float(val: str) -> float | None:
                    if not val or val in ("-", "n/a"):
                        return None
                    try:
                        return float(val)
                    except ValueError:
                        return None

                verdict_raw = row["verdict"].lower().strip()
                if verdict_raw not in valid_verdicts:
                    verdict_raw = "error"

                # Parse research_citations (backward compat: column may be absent)
                citations_raw = row.get("research_citations", "")
                citations = (
                    [c.strip() for c in citations_raw.split("|") if c.strip()]
                    if citations_raw
                    else []
                )

                records.append(ExperimentRecord(
                    id=int(row["id"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    hypothesis=row["hypothesis"],
                    change_summary=row["change_summary"],
                    issue_number=_safe_int(row["issue_number"]),
                    pr_number=_safe_int(row["pr_number"]),
                    score_before=_safe_float(row["score_before"]),
                    score_after=_safe_float(row["score_after"]),
                    delta=_safe_float(row["delta"]),
                    verdict=verdict_raw,  # type: ignore[arg-type]
                    cost_usd=_safe_float(row["cost_usd"]),
                    notes=row["notes"],
                    research_citations=citations,
                ))
        log.debug("load_history_complete", record_count=len(records))
        return records

    async def read_config(self) -> FactoryConfig:
        """Read .factory/config.json and return a FactoryConfig."""
        log.debug("read_config", path=str(self.factory_dir / "config.json"))
        config_path = self.factory_dir / "config.json"
        data = json.loads(config_path.read_text())
        return FactoryConfig(**data)

    async def save_eval_profile(self, profile: EvalProfile) -> None:
        """Write .factory/eval_profile.json."""
        log.info(
            "save_eval_profile",
            dimension_count=len(profile.dimensions),
            human_reviewed=profile.human_reviewed,
        )
        (self.factory_dir / "eval_profile.json").write_text(
            json.dumps(profile.model_dump(), indent=2) + "\n"
        )

    async def read_eval_profile(self) -> EvalProfile | None:
        """Read .factory/eval_profile.json, return None if missing."""
        profile_path = self.factory_dir / "eval_profile.json"
        if not profile_path.exists():
            log.debug("read_eval_profile_not_found", path=str(profile_path))
            return None
        data = json.loads(profile_path.read_text())
        log.debug("read_eval_profile_loaded", dimension_count=len(data.get("dimensions", [])))
        return EvalProfile(**data)

    async def read_strategy(self) -> str | None:
        """Read strategy/current.md, return None if missing."""
        strategy_path = self.factory_dir / "strategy" / "current.md"
        if not strategy_path.exists():
            log.debug("read_strategy_not_found")
            return None
        log.debug("read_strategy_loaded", path=str(strategy_path))
        return strategy_path.read_text()

    async def write_strategy(self, content: str) -> None:
        """Write strategy/current.md."""
        log.info("write_strategy", content_length=len(content))
        strategy_path = self.factory_dir / "strategy" / "current.md"
        strategy_path.parent.mkdir(parents=True, exist_ok=True)
        strategy_path.write_text(content)
