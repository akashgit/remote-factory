"""Tests for factory.research.runner — run phase orchestration."""

import json
from pathlib import Path

import pytest

from factory.models import ResearchTarget, ResultParseError, RunStatus
from factory.research.runner import (
    create_run_dir,
    execute_run,
    load_run_summary,
    parse_result,
)


def _config(
    tmp_path: Path,
    command: str = "echo ok",
    timeout: int = 30,
) -> ResearchTarget:
    """Create a ResearchTarget pointing at a result file in tmp_path."""
    return ResearchTarget(
        objective="test",
        metric="accuracy",
        target=0.9,
        run_command=command,
        result_path=str(tmp_path / "results.json"),
        result_parser="json",
        timeout=timeout,
    )


class TestExecuteRunSuccess:
    async def test_pass_with_metric(self, tmp_path: Path) -> None:
        result_path = tmp_path / "results.json"
        cmd = f'echo ok && echo \'{{"accuracy": 0.95}}\' > {result_path}'
        config = _config(tmp_path, command=cmd)

        result = await execute_run(tmp_path, config, "cycle-001")

        assert result.status == RunStatus.PASS
        assert result.metric_value == 0.95
        assert result.duration_seconds > 0
        assert result.artifacts_path.is_dir()
        assert "ok" in result.stdout

    async def test_artifacts_written(self, tmp_path: Path) -> None:
        result_path = tmp_path / "results.json"
        cmd = f'echo hello && echo \'{{"accuracy": 0.5}}\' > {result_path}'
        config = _config(tmp_path, command=cmd)

        result = await execute_run(tmp_path, config, "cycle-002")

        assert (result.artifacts_path / "stdout.log").exists()
        assert (result.artifacts_path / "stderr.log").exists()
        assert (result.artifacts_path / "summary.json").exists()

        summary = json.loads((result.artifacts_path / "summary.json").read_text())
        assert summary["status"] == "PASS"
        assert summary["metric_value"] == 0.5


class TestExecuteRunFailure:
    async def test_nonzero_exit(self, tmp_path: Path) -> None:
        config = _config(tmp_path, command="exit 1")

        result = await execute_run(tmp_path, config, "cycle-fail")

        assert result.status == RunStatus.FAIL
        assert result.metric_value == 0.0

    async def test_parse_error(self, tmp_path: Path) -> None:
        result_path = tmp_path / "results.json"
        cmd = f'echo \'{{"wrong_key": 1}}\' > {result_path}'
        config = _config(tmp_path, command=cmd)

        result = await execute_run(tmp_path, config, "cycle-parse-err")

        assert result.status == RunStatus.ERROR
        assert result.metric_value == 0.0


class TestExecuteRunTimeout:
    async def test_timeout_kills_process(self, tmp_path: Path) -> None:
        config = _config(tmp_path, command="sleep 60", timeout=1)

        result = await execute_run(tmp_path, config, "cycle-timeout")

        assert result.status == RunStatus.TIMEOUT
        assert result.metric_value == 0.0
        assert result.duration_seconds >= 1.0
        assert (result.artifacts_path / "summary.json").exists()


class TestParseResult:
    def test_dotted_path(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"results": {"accuracy": 0.85}}))
        assert parse_result(result_file, "json", "results.accuracy") == 0.85

    def test_ratio_metric(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"resolved": 3, "total": 4}))
        assert parse_result(result_file, "json", "resolved/total") == 0.75

    def test_boolean_rejected(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"flag": True}))
        with pytest.raises(ResultParseError, match="boolean"):
            parse_result(result_file, "json", "flag")

    def test_nan_rejected(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text('{"val": NaN}')
        with pytest.raises(ResultParseError):
            parse_result(result_file, "json", "val")

    def test_non_dict_rejected(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text("[1, 2, 3]")
        with pytest.raises(ResultParseError, match="not a JSON object"):
            parse_result(result_file, "json", "value")

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ResultParseError, match="not found"):
            parse_result(tmp_path / "missing.json", "json", "x")

    def test_invalid_json(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text("{broken")
        with pytest.raises(ResultParseError, match="invalid JSON"):
            parse_result(result_file, "json", "x")

    def test_unsupported_parser(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text("{}")
        with pytest.raises(ResultParseError, match="unsupported"):
            parse_result(result_file, "csv", "x")

    def test_ratio_zero_denominator(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"a": 5, "b": 0}))
        with pytest.raises(ResultParseError, match="zero"):
            parse_result(result_file, "json", "a/b")

    def test_ratio_empty_parts(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"a": 1}))
        with pytest.raises(ResultParseError, match="two non-empty"):
            parse_result(result_file, "json", "a/")

    def test_non_numeric_value(self, tmp_path: Path) -> None:
        result_file = tmp_path / "r.json"
        result_file.write_text(json.dumps({"val": "hello"}))
        with pytest.raises(ResultParseError, match="not numeric"):
            parse_result(result_file, "json", "val")


class TestCreateRunDir:
    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="path traversal"):
            create_run_dir(tmp_path, "../../etc")

    def test_slash_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="path traversal"):
            create_run_dir(tmp_path, "a/b")

    def test_valid_cycle_id(self, tmp_path: Path) -> None:
        run_dir = create_run_dir(tmp_path, "cycle-001")
        assert run_dir.exists()
        assert run_dir.name == "cycle-001"


class TestLoadRunSummary:
    def test_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".factory" / "research" / "runs" / "c1"
        runs_dir.mkdir(parents=True)
        (runs_dir / "summary.json").write_text("{broken")
        assert load_run_summary(runs_dir) is None

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".factory" / "research" / "runs" / "c1"
        runs_dir.mkdir(parents=True)
        assert load_run_summary(runs_dir) is None

    def test_valid_json(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / ".factory" / "research" / "runs" / "c1"
        runs_dir.mkdir(parents=True)
        data = {"status": "PASS", "metric_value": 0.9}
        (runs_dir / "summary.json").write_text(json.dumps(data))
        result = load_run_summary(runs_dir)
        assert result == data
