"""Tests for hard constraints — model, parser, and precheck integration."""

import json
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
