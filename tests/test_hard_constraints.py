"""Tests for hard constraints — model, parser, precheck, and finalize gate."""

import json
from argparse import Namespace
from pathlib import Path
from factory.models import FactoryConfig, HardConstraint
from factory.precheck import check_hard_constraints, run_precheck


class TestHardConstraintModel:
    def test_basic(self) -> None:
        hc = HardConstraint(name="test", check="echo ok")
        assert hc.name == "test"
        assert hc.check == "echo ok"
        assert hc.description == ""

    def test_with_description(self) -> None:
        hc = HardConstraint(name="test", check="true", description="a test constraint")
        assert hc.description == "a test constraint"

    def test_factory_config_has_field(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=["src/"],
            guards=["no secrets"],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=["be nice"],
            hard_constraints=[HardConstraint(name="q", check="true")],
        )
        assert len(config.hard_constraints) == 1
        assert config.hard_constraints[0].name == "q"

    def test_factory_config_default_empty(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
        )
        assert config.hard_constraints == []

    def test_serialization_roundtrip(self) -> None:
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            hard_constraints=[HardConstraint(name="q", check="bash check.sh", description="quality")],
        )
        data = json.loads(json.dumps(config.model_dump()))
        restored = FactoryConfig(**data)
        assert len(restored.hard_constraints) == 1
        assert restored.hard_constraints[0].name == "q"
        assert restored.hard_constraints[0].check == "bash check.sh"


class TestCheckHardConstraints:
    def test_passing_constraint(self, tmp_path: Path) -> None:
        constraints = [HardConstraint(name="always_pass", check="true")]
        results = check_hard_constraints(constraints, tmp_path)
        assert len(results) == 1
        assert results[0].passed is True
        assert "always_pass" in results[0].name

    def test_failing_constraint(self, tmp_path: Path) -> None:
        constraints = [HardConstraint(name="always_fail", check="false")]
        results = check_hard_constraints(constraints, tmp_path)
        assert len(results) == 1
        assert results[0].passed is False

    def test_multiple_constraints(self, tmp_path: Path) -> None:
        constraints = [
            HardConstraint(name="pass1", check="true"),
            HardConstraint(name="fail1", check="false"),
            HardConstraint(name="pass2", check="echo ok"),
        ]
        results = check_hard_constraints(constraints, tmp_path)
        assert len(results) == 3
        assert results[0].passed is True
        assert results[1].passed is False
        assert results[2].passed is True

    def test_empty_constraints(self, tmp_path: Path) -> None:
        results = check_hard_constraints([], tmp_path)
        assert results == []

    def test_timeout_constraint(self, tmp_path: Path) -> None:
        constraints = [HardConstraint(name="slow", check="sleep 10")]
        results = check_hard_constraints(constraints, tmp_path, timeout=1)
        assert len(results) == 1
        assert results[0].passed is False
        assert "timed out" in results[0].detail


class TestPrecheckWithHardConstraints:
    def test_hard_constraint_failure_blocks_precheck(self, tmp_path: Path) -> None:
        result = run_precheck(
            score_before=0.5,
            score_after=0.9,
            threshold=0.8,
            hypothesis="test hypothesis",
            history=[],
            project_path=tmp_path,
            hard_constraints=[HardConstraint(name="blocker", check="false")],
        )
        assert result.passed is False
        assert any("hard_constraint:blocker" in f for f in result.blocking_failures)

    def test_hard_constraint_pass_does_not_block(self, tmp_path: Path) -> None:
        result = run_precheck(
            score_before=0.5,
            score_after=0.9,
            threshold=0.8,
            hypothesis="test hypothesis",
            history=[],
            project_path=tmp_path,
            hard_constraints=[HardConstraint(name="ok", check="true")],
        )
        assert "hard_constraint:ok" not in result.blocking_failures

    def test_no_hard_constraints_is_fine(self, tmp_path: Path) -> None:
        result = run_precheck(
            score_before=0.5,
            score_after=0.9,
            threshold=0.8,
            hypothesis="test hypothesis",
            history=[],
            project_path=tmp_path,
        )
        assert all("hard_constraint" not in f for f in result.blocking_failures)


class TestParseHardConstraints:
    def test_parse_from_factory_md(self) -> None:
        from factory.store import _parse_hard_constraints

        items = [
            "name: quality_check\ncheck: bash quality.sh\ndescription: Must pass quality",
            "name: server_up\ncheck: curl -sf http://localhost:8080/ping",
        ]
        constraints = _parse_hard_constraints(items)
        assert len(constraints) == 2
        assert constraints[0].name == "quality_check"
        assert constraints[0].check == "bash quality.sh"
        assert constraints[0].description == "Must pass quality"
        assert constraints[1].name == "server_up"

    def test_skips_incomplete_items(self) -> None:
        from factory.store import _parse_hard_constraints

        items = ["name: no_check", "check: no_name"]
        constraints = _parse_hard_constraints(items)
        assert len(constraints) == 0

    def test_non_list_returns_empty(self) -> None:
        from factory.store import _parse_hard_constraints

        assert _parse_hard_constraints("not a list") == []
        assert _parse_hard_constraints(42.0) == []


def _make_project_with_config(tmp_path: Path, hard_constraints: list[dict] | None = None) -> Path:
    """Helper to create a project dir with .factory/config.json."""
    project = tmp_path / "proj"
    project.mkdir()
    factory_dir = project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "experiments").mkdir()
    (factory_dir / "strategy").mkdir()
    (factory_dir / "reviews").mkdir()
    config = {
        "goal": "test",
        "scope": [],
        "guards": [],
        "eval_command": "echo ok",
        "eval_threshold": 0.8,
        "constraints": [],
        "hard_constraints": hard_constraints or [],
    }
    (factory_dir / "config.json").write_text(json.dumps(config))
    tsv = factory_dir / "results.tsv"
    tsv.write_text("id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
                   "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n")
    return project


class TestForceFlag:
    def test_force_flag_bypasses_precheck(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = _make_project_with_config(tmp_path, [
            {"name": "always_fail", "check": "false", "description": ""},
        ])
        args = Namespace(
            path=str(project), id=1, verdict="keep",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
            force=True,
        )
        cmd_finalize(args)
        tsv = (project / ".factory" / "results.tsv").read_text()
        assert "keep" in tsv
        assert "OVERRIDDEN" not in tsv

    def test_force_flag_with_revert_is_noop(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = _make_project_with_config(tmp_path, [
            {"name": "always_fail", "check": "false", "description": ""},
        ])
        args = Namespace(
            path=str(project), id=1, verdict="revert",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
            force=True,
        )
        cmd_finalize(args)
        tsv = (project / ".factory" / "results.tsv").read_text()
        assert "revert" in tsv
        assert "OVERRIDDEN" not in tsv


class TestFinalizeGate:
    def test_keep_overridden_when_hard_constraint_fails(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = _make_project_with_config(tmp_path, [
            {"name": "always_fail", "check": "false", "description": ""},
        ])
        args = Namespace(
            path=str(project), id=1, verdict="keep",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
        )
        cmd_finalize(args)
        tsv = (project / ".factory" / "results.tsv").read_text()
        assert "revert" in tsv
        assert "OVERRIDDEN" in tsv

    def test_keep_allowed_when_constraints_pass(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = _make_project_with_config(tmp_path, [
            {"name": "always_pass", "check": "true", "description": ""},
        ])
        args = Namespace(
            path=str(project), id=1, verdict="keep",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
        )
        cmd_finalize(args)
        tsv = (project / ".factory" / "results.tsv").read_text()
        assert "keep" in tsv
        assert "OVERRIDDEN" not in tsv

    def test_revert_verdict_skips_precheck(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = _make_project_with_config(tmp_path, [
            {"name": "would_fail", "check": "false", "description": ""},
        ])
        args = Namespace(
            path=str(project), id=1, verdict="revert",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
        )
        cmd_finalize(args)
        tsv = (project / ".factory" / "results.tsv").read_text()
        assert "revert" in tsv
        assert "OVERRIDDEN" not in tsv

    def test_no_config_skips_precheck(self, tmp_path: Path) -> None:
        from factory.cli import cmd_finalize

        project = tmp_path / "proj"
        project.mkdir()
        factory_dir = project / ".factory"
        factory_dir.mkdir()
        (factory_dir / "experiments").mkdir()
        (factory_dir / "strategy").mkdir()
        (factory_dir / "reviews").mkdir()
        tsv = factory_dir / "results.tsv"
        tsv.write_text("id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
                       "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n")
        args = Namespace(
            path=str(project), id=1, verdict="keep",
            hypothesis="test hyp", summary="test", notes="",
            issue=None, pr=None, cost=None,
            score_before=0.5, score_after=0.9,
        )
        cmd_finalize(args)
        tsv_content = tsv.read_text()
        assert "keep" in tsv_content

