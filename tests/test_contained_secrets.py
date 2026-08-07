"""The gate in front of the only step that moves a working tree off this machine.

The design is warn-and-confirm, not block: a false positive on a test fixture must not stop work,
because an override people use reflexively protects nobody. That makes the *failure* directions the
interesting cases — an absent scanner must warn and proceed, and an unanswerable prompt must refuse
rather than hang or assume yes.

`gitleaks` is never actually invoked here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained.secrets import (
    Finding,
    ScanResult,
    build_scan_argv,
    confirm_upload,
    gitleaks_available,
    render_findings,
    scan,
)


def _report(entries: list[dict[str, object]]):
    """Make the patched `subprocess.run` write a gitleaks report where `scan` looks for it."""

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(argv[argv.index("--report-path") + 1]).write_text(json.dumps(entries))
        return subprocess.CompletedProcess([], 0, "", "")

    return _run


# --------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------


def test_the_scan_covers_the_working_tree_and_not_the_history(tmp_path: Path) -> None:
    """History is not what is being uploaded, and scanning it turns a five-second check into a
    minutes-long one reporting secrets that are already published — a different problem."""
    argv = build_scan_argv(tmp_path, tmp_path / "report.json")
    assert argv[:3] == ["gitleaks", "dir", str(tmp_path)]
    assert "--no-banner" in argv


def test_availability_is_a_path_lookup_not_an_invocation() -> None:
    with patch("factory.contained.secrets.shutil.which", return_value=None):
        assert gitleaks_available() is False
    with patch("factory.contained.secrets.shutil.which", return_value="/usr/bin/gitleaks"):
        assert gitleaks_available() is True


# --------------------------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------------------------


def test_without_gitleaks_the_tree_is_reported_unscanned_rather_than_clean(
    tmp_path: Path
) -> None:
    """"No findings" and "nothing looked" must never be the same answer."""
    with patch("factory.contained.secrets.gitleaks_available", return_value=False):
        result = scan(tmp_path)
    assert result.scanned is False
    assert "UNSCANNED" in result.detail


def test_a_clean_tree_is_scanned_with_no_findings(tmp_path: Path) -> None:
    """gitleaks writes no report at all when it finds nothing."""
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, "", "")):
        result = scan(tmp_path)
    assert result.scanned is True and result.findings == ()


def test_findings_are_reported_relative_to_the_workspace_root(tmp_path: Path) -> None:
    """The copy under ~/.factory-contained is an implementation detail: a user told to fix
    `.factory-contained/<run>/<project>/.env` edits a file regenerated on the next run, while the
    real one keeps being uploaded."""
    (tmp_path / ".env").write_text("KEY=x")
    entries = [{"File": str(tmp_path / ".env"), "StartLine": 3, "RuleID": "generic-api-key",
                "Description": "Generic API Key"}]
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", side_effect=_report(entries)):
        result = scan(tmp_path)
    assert result.findings == (Finding(".env", 3, "generic-api-key", "Generic API Key"),)
    assert result.detail == "1 finding(s)"


def test_a_finding_outside_the_root_keeps_its_reported_path(tmp_path: Path) -> None:
    entries = [{"File": "/etc/shadow", "StartLine": 1, "RuleID": "r", "Description": "d"}]
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", side_effect=_report(entries)):
        result = scan(tmp_path)
    assert result.findings[0].file == "/etc/shadow"


def test_non_dict_report_entries_are_skipped_rather_than_crashing_the_upload(
    tmp_path: Path
) -> None:
    entries = ["unexpected", {"File": "a", "StartLine": 1, "RuleID": "r", "Description": "d"}]
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", side_effect=_report(entries)):  # type: ignore[arg-type]
        result = scan(tmp_path)
    assert len(result.findings) == 1


def test_a_scanner_that_cannot_be_run_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    """`scan` never raises: an unscannable tree must not become an exception in the launch path."""
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", side_effect=OSError("exec format")):
        result = scan(tmp_path)
    assert result.scanned is False and "could not be run" in result.detail


def test_a_scan_that_times_out_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="gitleaks", timeout=600)):
        result = scan(tmp_path)
    assert result.scanned is False and "could not be run" in result.detail


def test_a_report_that_is_not_json_is_treated_as_unscanned(tmp_path: Path) -> None:
    """Reading a malformed report as "clean" would turn a broken scanner into a silent bypass."""

    def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(argv[argv.index("--report-path") + 1]).write_text("<html>error</html>")
        return subprocess.CompletedProcess([], 0, "", "")

    with patch("factory.contained.secrets.gitleaks_available", return_value=True), \
         patch("factory.contained.secrets.subprocess.run", side_effect=_run):
        result = scan(tmp_path)
    assert result.scanned is False


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def test_findings_are_rendered_precisely_enough_to_check_by_hand() -> None:
    rendered = render_findings(ScanResult(
        scanned=True,
        findings=(Finding(".env", 3, "generic-api-key", "Generic API Key"),),
        detail="1 finding(s)",
    ))
    assert ".env:3" in rendered and "generic-api-key" in rendered


# --------------------------------------------------------------------------------------------
# The confirmation, which is what actually gates the upload
# --------------------------------------------------------------------------------------------


def test_an_unscanned_tree_warns_and_proceeds(capsys: pytest.CaptureFixture[str]) -> None:
    """The absence of a scanner is not evidence of a secret; refusing would make an optional tool
    mandatory by the back door."""
    result = ScanResult(scanned=False, detail="gitleaks is not installed")
    assert confirm_upload(result, assume_yes=False, interactive=False) is True
    assert "Warning" in capsys.readouterr().err


def test_a_clean_tree_asks_nothing() -> None:
    """A prompt on every clean run is a prompt people learn to dismiss."""
    with patch("builtins.input") as ask:
        assert confirm_upload(ScanResult(scanned=True), assume_yes=False, interactive=True) is True
    ask.assert_not_called()


def _flagged() -> ScanResult:
    return ScanResult(
        scanned=True,
        findings=(Finding(".env", 1, "generic-api-key", "Generic API Key"),),
        detail="1 finding(s)",
    )


def test_findings_are_shown_before_the_question(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("builtins.input", return_value="y"):
        assert confirm_upload(_flagged(), assume_yes=False, interactive=True) is True
    err = capsys.readouterr().err
    assert ".env:1" in err
    assert "copied onto cluster storage" in err


def test_yes_overrides_the_findings_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    """The override is for automation, and it is recorded in the run's evidence."""
    with patch("builtins.input") as ask:
        assert confirm_upload(_flagged(), assume_yes=True) is True
    ask.assert_not_called()
    assert "--yes was given" in capsys.readouterr().err


def test_findings_with_nobody_to_ask_refuse_the_upload(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a hang, and not an assumed yes: the tree stays on this machine."""
    with patch("builtins.input") as ask:
        assert confirm_upload(_flagged(), assume_yes=False, interactive=False) is False
    ask.assert_not_called()
    assert "Refusing to upload" in capsys.readouterr().err


@pytest.mark.parametrize(("answer", "proceed"), [("y", True), ("YES", True), ("n", False),
                                                 ("", False), ("maybe", False)])
def test_only_an_affirmative_answer_proceeds(answer: str, proceed: bool) -> None:
    """Anything that is not an explicit yes is a no — the default has to be the safe direction."""
    with patch("builtins.input", return_value=answer):
        assert confirm_upload(_flagged(), assume_yes=False, interactive=True) is proceed


def test_interactivity_is_detected_from_the_terminal_when_not_stated() -> None:
    with patch("sys.stdin.isatty", return_value=False):
        assert confirm_upload(_flagged(), assume_yes=False) is False
