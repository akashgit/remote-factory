"""Tests for factory.optimization.executors — protocol conformance and basic behavior."""

from __future__ import annotations

from factory.optimization.executors import (
    FactoryCeoExecutor,
    HarborExecutor,
    WorkflowRunExecutor,
)
from factory.optimization.protocols import Executor


class TestExecutorProtocolConformance:
    def test_ceo_executor(self) -> None:
        assert isinstance(FactoryCeoExecutor(), Executor)

    def test_harbor_executor(self) -> None:
        assert isinstance(HarborExecutor(), Executor)

    def test_workflow_run_executor(self) -> None:
        assert isinstance(WorkflowRunExecutor("test"), Executor)


class TestFactoryCeoExecutor:
    def test_defaults(self) -> None:
        e = FactoryCeoExecutor()
        assert e.mode == "improve"
        assert e.extra_args == []

    def test_custom_mode(self) -> None:
        e = FactoryCeoExecutor(mode="research", extra_args=["--headless"])
        assert e.mode == "research"
        assert e.extra_args == ["--headless"]


class TestHarborExecutor:
    def test_defaults(self) -> None:
        e = HarborExecutor()
        assert e.harbor_script == "./run-harbor.sh"
        assert e.skill_env_var == "HARBOR_SKILL_PATH"

    def test_missing_script_returns_error(self, tmp_path) -> None:
        e = HarborExecutor(harbor_script="nonexistent.sh")
        from factory.optimization.surface import Surface
        result = e.execute(tmp_path, Surface())
        assert result.returncode == 1
