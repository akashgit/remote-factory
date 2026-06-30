"""Tests for the eval_architecture() hygiene dimension."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
    _run_sentrux_scan,
    eval_architecture,
)


def test_neutral_when_no_rules_toml(tmp_path: Path) -> None:
    result = eval_architecture(tmp_path)
    assert result["name"] == "architecture"
    assert result["score"] == 0.5
    assert result["passed"] is True
    assert "no .sentrux/rules.toml found" in result["details"]


def test_neutral_when_sentrux_not_installed(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    with patch("factory.eval.hygiene.shutil.which", return_value=None):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.5
    assert result["passed"] is True
    assert "sentrux not installed" in result["details"]


def test_pass_with_full_quality(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    mock_output = json.dumps({"quality_signal": 10000, "bottleneck": "none"})
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=mock_output, stderr="")

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", return_value=completed),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 1.0
    assert result["passed"] is True
    assert "quality_signal=10000" in result["details"]


def test_partial_quality_score(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    mock_output = json.dumps({"quality_signal": 7342, "bottleneck": "modularity"})
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=mock_output, stderr="")

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", return_value=completed),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.7342
    assert result["passed"] is False
    assert "bottleneck=modularity" in result["details"]


def test_parse_error_with_exit_zero(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="All rules pass", stderr=""
    )

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", return_value=completed),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 1.0
    assert result["passed"] is True
    assert "All constraints satisfied" in result["details"]


def test_parse_error_with_exit_nonzero(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="VIOLATION: max_cc exceeded", stderr=""
    )

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", return_value=completed),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.0
    assert result["passed"] is False
    assert "Rule violations" in result["details"]


def test_timeout_returns_neutral(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch(
            "factory.eval.hygiene.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="sentrux", timeout=120),
        ),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.5
    assert result["passed"] is True
    assert "Timeout" in result["details"]


def test_hygiene_weights_sum_to_one() -> None:
    total = sum(HYGIENE_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"HYGIENE_WEIGHTS sum to {total}, expected 1.0"
    assert "architecture" in HYGIENE_WEIGHTS


# ── sentrux scan parsing ──────────────────────────────────────


def test_scan_parses_all_five_metrics(tmp_path: Path) -> None:
    scan_output = json.dumps({
        "modularity": 0.85,
        "acyclicity": 1.0,
        "depth": 0.72,
        "equality": 0.45,
        "redundancy": 0.90,
    })
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=scan_output, stderr="")

    with patch("factory.eval.hygiene.subprocess.run", return_value=completed):
        result = _run_sentrux_scan(tmp_path)

    assert result is not None
    assert result["modularity"] == 0.85
    assert result["acyclicity"] == 1.0
    assert result["depth"] == 0.72
    assert result["equality"] == 0.45
    assert result["redundancy"] == 0.90


def test_scan_returns_none_on_invalid_json(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")

    with patch("factory.eval.hygiene.subprocess.run", return_value=completed):
        result = _run_sentrux_scan(tmp_path)

    assert result is None


def test_scan_returns_none_on_timeout(tmp_path: Path) -> None:
    with patch(
        "factory.eval.hygiene.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="sentrux", timeout=120),
    ):
        result = _run_sentrux_scan(tmp_path)

    assert result is None


def test_scan_returns_none_when_no_metrics(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")

    with patch("factory.eval.hygiene.subprocess.run", return_value=completed):
        result = _run_sentrux_scan(tmp_path)

    assert result is None


def test_scan_partial_metrics(tmp_path: Path) -> None:
    scan_output = json.dumps({"equality": 0.33, "modularity": 0.91})
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=scan_output, stderr="")

    with patch("factory.eval.hygiene.subprocess.run", return_value=completed):
        result = _run_sentrux_scan(tmp_path)

    assert result is not None
    assert result["equality"] == 0.33
    assert result["modularity"] == 0.91
    assert "depth" not in result


def test_eval_architecture_includes_scan_metrics(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    check_output = json.dumps({"quality_signal": 8500, "bottleneck": "none"})
    scan_output = json.dumps({
        "modularity": 0.9,
        "acyclicity": 1.0,
        "depth": 0.8,
        "equality": 0.5,
        "redundancy": 0.95,
    })
    check_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=check_output, stderr="")
    scan_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=scan_output, stderr="")

    def mock_run(cmd, **kwargs):
        if "scan" in cmd:
            return scan_completed
        return check_completed

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", side_effect=mock_run),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.85
    assert result["passed"] is True
    assert "scan_metrics" in result
    assert result["scan_metrics"]["equality"] == 0.5
    assert result["scan_metrics"]["modularity"] == 0.9


def test_eval_architecture_no_scan_metrics_on_scan_failure(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".sentrux"
    rules_dir.mkdir()
    (rules_dir / "rules.toml").write_text("[constraints]\nmax_cc = 30\n")

    check_output = json.dumps({"quality_signal": 9000, "bottleneck": "none"})
    check_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=check_output, stderr="")

    call_count = [0]
    def mock_run(cmd, **kwargs):
        call_count[0] += 1
        if "scan" in cmd:
            raise subprocess.TimeoutExpired(cmd="sentrux", timeout=120)
        return check_completed

    with (
        patch("factory.eval.hygiene.shutil.which", return_value="/usr/bin/sentrux"),
        patch("factory.eval.hygiene.subprocess.run", side_effect=mock_run),
    ):
        result = eval_architecture(tmp_path)

    assert result["score"] == 0.9
    assert "scan_metrics" not in result
