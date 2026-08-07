"""Scanning a workspace for secrets before it leaves the machine.

The k8s path copies a developer's working tree onto cluster storage, and a `.env` or a stray key
file goes with it. [Gitleaks](https://github.com/gitleaks/gitleaks) runs over the packed tree —
regex-based, fully offline, no network calls, which matters for a step whose whole purpose is
preventing exposure.

**Warn and confirm, not block.** A false positive on a test fixture must not stop work, because an
override people use reflexively protects nobody. `--yes` skips the prompt for automation and is
recorded in the run's evidence. When gitleaks is absent, `verify` says so and the upload warns that
it is unscanned rather than silently proceeding.

Not applied to the local target: nothing leaves the machine there, and a confirmation prompt people
learn to dismiss on every local run devalues the one that matters.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

GITLEAKS = "gitleaks"


@dataclass(frozen=True)
class Finding:
    """One secret gitleaks believes it found, located precisely enough to check by hand."""

    file: str
    line: int
    rule: str
    description: str


@dataclass(frozen=True)
class ScanResult:
    scanned: bool
    findings: tuple[Finding, ...] = ()
    detail: str = ""


def gitleaks_available() -> bool:
    return shutil.which(GITLEAKS) is not None


def build_scan_argv(path: Path, report: Path) -> list[str]:
    """`gitleaks dir` — the working tree as it will be packed, not the git history.

    History is not what is being uploaded, and scanning it turns a five-second check into a
    minutes-long one that reports secrets already published, which is a different problem.
    `--no-banner` keeps the report readable; the exit code carries the answer.
    """
    return [
        GITLEAKS, "dir", str(path),
        "--report-format", "json", "--report-path", str(report),
        "--no-banner", "--exit-code", "2",
    ]


def scan(path: Path) -> ScanResult:
    """Scan a directory. Never raises: an unscannable tree is a warning, not a failure."""
    if not gitleaks_available():
        return ScanResult(
            scanned=False,
            detail=(
                "gitleaks is not installed, so the workspace is being uploaded UNSCANNED. Install "
                "it (`brew install gitleaks`) to have this checked."
            ),
        )
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "gitleaks.json"
        try:
            subprocess.run(build_scan_argv(path, report), capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ScanResult(scanned=False, detail=f"gitleaks could not be run: {exc}")
        if not report.exists():
            return ScanResult(scanned=True, detail="no secrets found")
        try:
            payload = json.loads(report.read_text() or "[]")
        except json.JSONDecodeError:
            return ScanResult(scanned=False, detail="gitleaks produced a report that isn't JSON")

    findings = tuple(
        Finding(
            # Relative to the workspace root, not the absolute path of the *copy*. The copy is an
            # implementation detail under ~/.factory-contained; a user told to fix
            # `.factory-contained/<run>/<project>/.env` goes and edits a file that is regenerated on the
            # next run, while the real one keeps being uploaded.
            file=_relative(str(item.get("File", "?")), path),
            line=int(item.get("StartLine", 0) or 0),
            rule=str(item.get("RuleID", "?")),
            description=str(item.get("Description", "")),
        )
        for item in payload
        if isinstance(item, dict)
    )
    return ScanResult(
        scanned=True,
        findings=findings,
        detail="no secrets found" if not findings else f"{len(findings)} finding(s)",
    )


def _relative(reported: str, root: Path) -> str:
    try:
        return str(Path(reported).resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return reported


def render_findings(result: ScanResult) -> str:
    lines = [f"gitleaks: {result.detail}"]
    for finding in result.findings:
        lines.append(f"  {finding.file}:{finding.line}  [{finding.rule}] {finding.description}")
    return "\n".join(lines)


def confirm_upload(
    result: ScanResult, *, assume_yes: bool, interactive: bool | None = None
) -> bool:
    """Ask before uploading a tree gitleaks flagged. Returns whether to proceed.

    An unscanned tree warns and proceeds — the absence of a scanner is not evidence of a secret, and
    refusing to run without an optional tool would make it mandatory by the back door.
    """
    if not result.scanned:
        print(f"Warning: {result.detail}", file=sys.stderr)
        return True
    if not result.findings:
        return True

    print(render_findings(result), file=sys.stderr)
    print(
        "\nThis workspace is about to be copied onto cluster storage. Anything above goes with it.",
        file=sys.stderr,
    )
    if assume_yes:
        log.warning("secret_scan_overridden", findings=len(result.findings), reason="--yes")
        print("Proceeding anyway: --yes was given.", file=sys.stderr)
        return True
    is_interactive = sys.stdin.isatty() if interactive is None else interactive
    if not is_interactive:
        print(
            "Refusing to upload without confirmation. Re-run with --yes to proceed "
            "non-interactively.",
            file=sys.stderr,
        )
        return False
    answer = input("Upload anyway? [y/N] ")
    proceed = answer.strip().lower() in ("y", "yes")
    log.info("secret_scan_decision", findings=len(result.findings), proceed=proceed)
    return proceed
