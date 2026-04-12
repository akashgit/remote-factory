"""Experiment filesystem store — manages .factory/ directory structure."""

import csv
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal

from factory.models import CompositeScore, EvalProfile, ExperimentRecord, FactoryConfig


TSV_COLUMNS = [
    "id", "timestamp", "hypothesis", "change_summary", "issue_number",
    "pr_number", "score_before", "score_after", "delta", "verdict",
    "cost_usd", "notes",
]


class ExperimentStore:
    """Manages the .factory/ directory for a project."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.factory_dir = project_path / ".factory"

    async def init(self, config: FactoryConfig) -> None:
        """Create .factory/ structure with config.json, results.tsv, experiments/, strategy/."""
        self.factory_dir.mkdir(exist_ok=True)
        (self.factory_dir / "experiments").mkdir(exist_ok=True)
        (self.factory_dir / "strategy").mkdir(exist_ok=True)
        (self.factory_dir / "agents").mkdir(exist_ok=True)

        (self.factory_dir / "config.json").write_text(
            json.dumps(config.model_dump(), indent=2) + "\n"
        )

        tsv_path = self.factory_dir / "results.tsv"
        if not tsv_path.exists():
            buf = io.StringIO()
            writer = csv.writer(buf, dialect="excel-tab")
            writer.writerow(TSV_COLUMNS)
            tsv_path.write_text(buf.getvalue())

    async def reparse_config(self) -> FactoryConfig:
        """Re-read factory.md from project root, regenerate config.json."""
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
            elif stripped and current_section and not list_buffer:
                if current_section == "eval_threshold":
                    parsed[current_section] = float(stripped)
                else:
                    parsed[current_section] = stripped
        _flush_list()

        config = FactoryConfig(
            goal=str(parsed.get("goal", "")),
            scope=list(parsed.get("scope", [])),  # type: ignore[arg-type]
            guards=list(parsed.get("guards", [])),  # type: ignore[arg-type]
            eval_command=str(parsed.get("eval_command", "")),
            eval_threshold=float(parsed.get("eval_threshold", 0.0)),  # type: ignore[arg-type]
            constraints=list(parsed.get("constraints", [])),  # type: ignore[arg-type]
        )

        (self.factory_dir / "config.json").write_text(
            json.dumps(config.model_dump(), indent=2) + "\n"
        )
        return config

    async def next_id(self) -> int:
        """Return max existing experiment ID + 1, or 1 if none exist."""
        experiments_dir = self.factory_dir / "experiments"
        if not experiments_dir.exists():
            return 1
        ids = [
            int(d.name)
            for d in experiments_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]
        return max(ids) + 1 if ids else 1

    async def begin(self, hypothesis: str) -> int:
        """Create experiments/NNN/hypothesis.md, return the experiment ID.

        Idempotent: if the next experiment dir already exists (e.g. from a
        previous interrupted run), return its ID without crashing.
        """
        exp_id = await self.next_id()
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
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        filename = f"eval_{phase}.json"
        (exp_dir / filename).write_text(
            json.dumps(score.model_dump(), indent=2, default=str) + "\n"
        )

    async def save_diff(self, exp_id: int) -> None:
        """Capture git diff HEAD~1 into changes.diff."""
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
        """
        exp_dir = self.factory_dir / "experiments" / f"{exp_id:03d}"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "verdict.json").write_text(
            json.dumps(record.model_dump(), indent=2, default=str) + "\n"
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
                record.delta if record.delta is not None else "",
                record.verdict,
                record.cost_usd if record.cost_usd is not None else "",
                record.notes,
            ])

    async def load_history(self) -> list[ExperimentRecord]:
        """Parse results.tsv into a list of ExperimentRecords."""
        tsv_path = self.factory_dir / "results.tsv"
        if not tsv_path.exists():
            return []

        records: list[ExperimentRecord] = []
        with open(tsv_path, newline="") as f:
            reader = csv.DictReader(f, dialect="excel-tab")
            for row in reader:
                records.append(ExperimentRecord(
                    id=int(row["id"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    hypothesis=row["hypothesis"],
                    change_summary=row["change_summary"],
                    issue_number=int(row["issue_number"]) if row["issue_number"] else None,
                    pr_number=int(row["pr_number"]) if row["pr_number"] else None,
                    score_before=float(row["score_before"]) if row["score_before"] else None,
                    score_after=float(row["score_after"]) if row["score_after"] else None,
                    delta=float(row["delta"]) if row["delta"] else None,
                    verdict=row["verdict"],  # type: ignore[arg-type]
                    cost_usd=float(row["cost_usd"]) if row["cost_usd"] else None,
                    notes=row["notes"],
                ))
        return records

    async def read_config(self) -> FactoryConfig:
        """Read .factory/config.json and return a FactoryConfig."""
        config_path = self.factory_dir / "config.json"
        data = json.loads(config_path.read_text())
        return FactoryConfig(**data)

    async def save_eval_profile(self, profile: EvalProfile) -> None:
        """Write .factory/eval_profile.json."""
        (self.factory_dir / "eval_profile.json").write_text(
            json.dumps(profile.model_dump(), indent=2) + "\n"
        )

    async def read_eval_profile(self) -> EvalProfile | None:
        """Read .factory/eval_profile.json, return None if missing."""
        profile_path = self.factory_dir / "eval_profile.json"
        if not profile_path.exists():
            return None
        data = json.loads(profile_path.read_text())
        return EvalProfile(**data)

    async def read_strategy(self) -> str | None:
        """Read strategy/current.md, return None if missing."""
        strategy_path = self.factory_dir / "strategy" / "current.md"
        if not strategy_path.exists():
            return None
        return strategy_path.read_text()

    async def write_strategy(self, content: str) -> None:
        """Write strategy/current.md."""
        strategy_path = self.factory_dir / "strategy" / "current.md"
        strategy_path.parent.mkdir(parents=True, exist_ok=True)
        strategy_path.write_text(content)
