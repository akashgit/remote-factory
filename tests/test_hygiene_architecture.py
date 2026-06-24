"""Tests for the eval_architecture() hygiene dimension."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.eval.hygiene import (
    HYGIENE_WEIGHTS,
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
