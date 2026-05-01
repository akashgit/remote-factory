"""Tests for factory.research.runner — run phase orchestration."""

import json
from pathlib import Path

from factory.models import ResearchTarget
from factory.research.models import RunStatus
from factory.research.runner import execute_run


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
