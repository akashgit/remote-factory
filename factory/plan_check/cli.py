"""CLI subcommand for plan-check acceptance criteria verification."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

from factory.plan_check.criteria_extractor import parse_and_extract
from factory.plan_check.models import VerificationReport
from factory.plan_check.reporter import generate_report, to_json, to_markdown
from factory.plan_check.verifier import verify_plan

log = structlog.get_logger()


class PlanCheckError(Exception):
    """Raised when plan-check encounters a configuration or runtime error."""


def add_subcommand(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "plan-check",
        help="Verify acceptance criteria from the strategy plan",
    )
    parser.add_argument(
        "project_path",
        type=Path,
        help="Path to the project being checked",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline git SHA (defaults to .factory/config.json target_branch)",
    )
    parser.add_argument(
        "--strategy",
        type=Path,
        default=None,
        help="Path to strategy plan (defaults to .factory/strategy/current.md)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write reports (defaults to .factory/reports/)",
    )
    parser.add_argument(
        "--format",
        dest="report_format",
        choices=["json", "markdown", "both"],
        default="both",
        help="Report format (default: both)",
    )
    parser.add_argument(
        "--json",
        dest="json_stdout",
        action="store_true",
        default=False,
        help="Print JSON to stdout (for piping to CEO)",
    )
    parser.set_defaults(handler=_handle_plan_check)


def _handle_plan_check(args: argparse.Namespace) -> None:
    project_path = args.project_path.resolve()
    strategy_path = args.strategy or (project_path / ".factory" / "strategy" / "current.md")
    output_dir = args.output_dir or (project_path / ".factory" / "reports")

    try:
        report = run_plan_check(
            project_path=project_path,
            baseline=args.baseline,
            strategy_path=strategy_path,
            output_dir=output_dir,
            report_format=args.report_format,
        )
    except PlanCheckError as exc:
        log.error("plan_check_error", detail=str(exc))
        sys.exit(2)
    except Exception:
        log.exception("plan_check_error")
        sys.exit(2)

    if args.json_stdout:
        print(to_json(report))

    if report.summary.all_passed:
        sys.exit(0)
    else:
        sys.exit(1)


def run_plan_check(
    project_path: Path,
    baseline: str | None = None,
    strategy_path: Path | None = None,
    output_dir: Path | None = None,
    report_format: str = "both",
) -> VerificationReport:
    if strategy_path is None:
        strategy_path = project_path / ".factory" / "strategy" / "current.md"
    if output_dir is None:
        output_dir = project_path / ".factory" / "reports"

    if not strategy_path.exists():
        raise PlanCheckError(f"Strategy file not found: {strategy_path}")

    log.info("reading_strategy_plan", path=str(strategy_path))
    content = strategy_path.read_text()

    log.info("parsing_plan")
    hypotheses_with_criteria = parse_and_extract(content)

    total_criteria = sum(len(criteria) for _, criteria in hypotheses_with_criteria)
    log.info(
        "parsed_plan",
        hypotheses=len(hypotheses_with_criteria),
        total_criteria=total_criteria,
    )

    log.info("verifying_criteria", project_path=str(project_path))
    report = verify_plan(hypotheses_with_criteria, project_path)

    report = generate_report(report)

    for verdict in report.hypotheses:
        passed_count = sum(1 for c in verdict.criteria if c.passed)
        total_count = len(verdict.criteria)
        log.info(
            "hypothesis_result",
            hypothesis_id=verdict.hypothesis_id,
            passed=verdict.passed,
            criteria=f"{passed_count}/{total_count}",
        )

    if report_format in ("both", "markdown"):
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / "acceptance-verification.md"
        md_path.write_text(to_markdown(report))
        log.info("wrote_markdown_report", path=str(md_path))

    if report_format in ("both", "json"):
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "acceptance-verification.json"
        json_path.write_text(to_json(report))
        log.info("wrote_json_report", path=str(json_path))

    log.info(
        "plan_check_complete",
        all_passed=report.summary.all_passed,
        pass_rate=f"{report.summary.pass_rate:.0%}",
    )

    return report
