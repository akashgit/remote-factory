"""Tests for hard constraints in precheck and factory.md parsing."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.models import HardConstraint
from factory.precheck import check_hard_constraints, run_precheck


# ── check_hard_constraints ───────────────────────────────────


class TestCheckHardConstraints:
    @patch("factory.precheck.subprocess.run")
    def test_single_passing(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0)
        constraints = [HardConstraint(name="health", check="curl -sf http://localhost/ping")]
        results = check_hard_constraints(constraints, Path("/tmp"))
        assert len(results) == 1
        assert results[0].passed
        assert results[0].name == "hard_constraint:health"

    @patch("factory.precheck.subprocess.run")
    def test_single_failing(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=1, stderr="connection refused", stdout="")
        constraints = [
            HardConstraint(name="health", check="curl -sf http://localhost/ping",
                           description="Server must respond"),
        ]
        results = check_hard_constraints(constraints, Path("/tmp"))
        assert len(results) == 1
        assert not results[0].passed
        assert "connection refused" in results[0].detail
        assert "Server must respond" in results[0].detail

    @patch("factory.precheck.subprocess.run")
    def test_multiple_mixed(self, mock_run: MagicMock):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr="fail", stdout=""),
        ]
        constraints = [
            HardConstraint(name="check_a", check="echo ok"),
            HardConstraint(name="check_b", check="false"),
        ]
        results = check_hard_constraints(constraints, Path("/tmp"))
        assert len(results) == 2
        assert results[0].passed
        assert not results[1].passed

    @patch("factory.precheck.subprocess.run")
    def test_timeout(self, mock_run: MagicMock):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=120)
        constraints = [HardConstraint(name="slow", check="sleep 999")]
        results = check_hard_constraints(constraints, Path("/tmp"))
        assert len(results) == 1
        assert not results[0].passed
        assert "timed out" in results[0].detail

    def test_empty_list(self):
        results = check_hard_constraints([], Path("/tmp"))
        assert results == []

    @patch("factory.precheck.subprocess.run")
    def test_uses_project_path_as_cwd(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0)
        project = Path("/my/project")
        constraints = [HardConstraint(name="test", check="echo hi")]
        check_hard_constraints(constraints, project)
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == project


# ── run_precheck integration with hard_constraints ───────────


class TestRunPrecheckWithHardConstraints:
    def test_passes_without_constraints(self):
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="add feature",
            history=[],
            project_path=Path("/tmp"),
            smoke_test_command="",
            hard_constraints=None,
        )
        assert result.passed
        # No hard_constraint checks should appear
        names = [c.name for c in result.checks]
        assert not any(n.startswith("hard_constraint:") for n in names)

    @patch("factory.precheck.subprocess.run")
    def test_passing_constraint(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=0)
        constraints = [HardConstraint(name="quality", check="bash check.sh")]
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="add feature",
            history=[],
            project_path=Path("/tmp"),
            smoke_test_command="",
            hard_constraints=constraints,
        )
        assert result.passed
        names = [c.name for c in result.checks]
        assert "hard_constraint:quality" in names

    @patch("factory.precheck.subprocess.run")
    def test_failing_constraint_blocks(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(returncode=1, stderr="quality check failed", stdout="")
        constraints = [HardConstraint(name="quality", check="bash check.sh")]
        result = run_precheck(
            score_before=0.7,
            score_after=0.85,
            threshold=0.8,
            hypothesis="add feature",
            history=[],
            project_path=Path("/tmp"),
            smoke_test_command="",
            hard_constraints=constraints,
        )
        assert not result.passed
        assert "hard_constraint:quality" in result.blocking_failures


# ── factory.md parsing ───────────────────────────────────────


class TestHardConstraintParsing:
    def test_parses_hard_constraints_from_factory_md(self, tmp_path: Path):
        factory_md = tmp_path / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n"
            "## Scope\n### Modifiable\n- src/**\n"
            "## Guards\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
            "## Constraints\n"
            "## Hard Constraints\n"
            "- name: quality_gates\n"
            "  check: bash quality_smoke.sh\n"
            "  description: Quality scores must be non-null\n"
            "- name: server_responds\n"
            "  check: curl -sf http://localhost:8080/ping\n"
            "  description: Server must be healthy\n"
        )
        (tmp_path / ".factory").mkdir()

        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_path)
        config = asyncio.run(store.reparse_config())
        assert len(config.hard_constraints) == 2
        assert config.hard_constraints[0].name == "quality_gates"
        assert config.hard_constraints[0].check == "bash quality_smoke.sh"
        assert config.hard_constraints[0].description == "Quality scores must be non-null"
        assert config.hard_constraints[1].name == "server_responds"
        assert config.hard_constraints[1].check == "curl -sf http://localhost:8080/ping"

    def test_empty_hard_constraints(self, tmp_path: Path):
        factory_md = tmp_path / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n"
            "## Scope\n### Modifiable\n- src/**\n"
            "## Guards\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
            "## Constraints\n"
        )
        (tmp_path / ".factory").mkdir()

        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_path)
        config = asyncio.run(store.reparse_config())
        assert config.hard_constraints == []

    def test_hard_constraints_in_config_json(self, tmp_path: Path):
        """Verify hard constraints survive serialization to config.json and back."""
        factory_md = tmp_path / "factory.md"
        factory_md.write_text(
            "## Goal\nTest project\n"
            "## Scope\n### Modifiable\n- src/**\n"
            "## Guards\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
            "## Constraints\n"
            "## Hard Constraints\n"
            "- name: smoke\n"
            "  check: echo ok\n"
        )
        (tmp_path / ".factory").mkdir()

        from factory.store import ExperimentStore

        store = ExperimentStore(tmp_path)
        config = asyncio.run(store.reparse_config())
        assert len(config.hard_constraints) == 1

        # Read back from config.json
        config_roundtrip = asyncio.run(store.read_config())
        assert len(config_roundtrip.hard_constraints) == 1
        assert config_roundtrip.hard_constraints[0].name == "smoke"
        assert config_roundtrip.hard_constraints[0].check == "echo ok"
