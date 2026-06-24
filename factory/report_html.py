"""HTML experiment report rendering — Layer 1 pure tool."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class EvalRow:
    dimension: str
    before: float
    after: float


def generate_experiment_report(
    project_path: Path,
    exp_id: str,
    output_path: Path | None = None,
) -> Path:
    """Render an HTML report for a single experiment.

    Reads experiment data from .factory/experiments/<exp_id>/ and renders
    a self-contained HTML report with inlined Pico CSS.

    Returns the path to the generated HTML file.
    """
    exp_dir = project_path / ".factory" / "experiments" / exp_id
    if not exp_dir.is_dir():
        raise FileNotFoundError(
            f"Experiment directory not found: {exp_dir}"
        )

    hypothesis = _read_text(exp_dir / "hypothesis.md")
    eval_before = _read_json(exp_dir / "eval_before.json")
    eval_after = _read_json(exp_dir / "eval_after.json")
    changes_diff = _read_text(exp_dir / "changes.diff")
    verdict_data = _read_json(exp_dir / "verdict.json")

    verdict = verdict_data.get("verdict", "") if verdict_data else ""
    verdict_rationale = verdict_data.get("rationale", "") if verdict_data else ""
    date = verdict_data.get("date", "") if verdict_data else ""

    eval_results, composite_before, composite_after = _build_eval_table(
        eval_before, eval_after,
    )

    changes_summary = _build_changes_summary(changes_diff)

    pico_css_path = _TEMPLATES_DIR / "pico.classless.min.css"
    pico_css = pico_css_path.read_text() if pico_css_path.exists() else ""

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
    )
    template = env.get_template("experiment_report.html.j2")

    html = template.render(
        exp_id=exp_id,
        project_name=project_path.name,
        verdict=verdict,
        date=date,
        hypothesis=hypothesis,
        eval_results=eval_results,
        composite_before=composite_before,
        composite_after=composite_after,
        changes_summary=changes_summary,
        verdict_rationale=verdict_rationale,
        pico_css=pico_css,
    )

    if output_path is None:
        reports_dir = project_path / ".factory" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = reports_dir / f"experiment-{exp_id}.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)
    log.info("report_generated", path=str(output_path), experiment=exp_id)
    return output_path


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text()
    return ""


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            log.warning("invalid_json", path=str(path))
    return {}


def _build_eval_table(
    before: dict, after: dict,
) -> tuple[list[EvalRow], float | None, float | None]:
    """Build eval comparison rows from before/after JSON."""
    rows: list[EvalRow] = []
    composite_before: float | None = None
    composite_after: float | None = None

    before_results = _extract_results(before)
    after_results = _extract_results(after)

    all_dims = sorted(set(before_results) | set(after_results))
    for dim in all_dims:
        rows.append(EvalRow(
            dimension=dim,
            before=before_results.get(dim, 0.0),
            after=after_results.get(dim, 0.0),
        ))

    if "composite" in before:
        composite_before = float(before["composite"])
    if "composite" in after:
        composite_after = float(after["composite"])

    return rows, composite_before, composite_after


def _extract_results(data: dict) -> dict[str, float]:
    """Extract dimension→score mapping from eval JSON."""
    results: dict[str, float] = {}
    for item in data.get("results", []):
        name = item.get("name", item.get("dimension", ""))
        score = item.get("score", item.get("value", 0.0))
        if name:
            results[name] = float(score)
    return results


def _build_changes_summary(diff_text: str) -> str:
    """Build a human-readable summary from a diff."""
    if not diff_text:
        return ""

    files_changed: list[str] = []
    additions = 0
    deletions = 0

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4:
                files_changed.append(parts[3].lstrip("b/"))
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    parts_out = []
    if files_changed:
        parts_out.append(f"{len(files_changed)} file(s) changed: {', '.join(files_changed)}")
    if additions or deletions:
        parts_out.append(f"+{additions} / -{deletions} lines")

    return "; ".join(parts_out) if parts_out else "No changes recorded."
