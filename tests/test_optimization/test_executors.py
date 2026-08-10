"""Tests for factory.optimization.executors — protocol conformance and basic behavior."""

from __future__ import annotations

import base64
import json

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

    def test_constructor_params(self) -> None:
        e = HarborExecutor(
            docker_host="unix:///run/user/1002/podman/podman.sock",
            git_ref="abc123",
            n_tasks=10,
            concurrency=4,
            split="test",
        )
        assert e.docker_host == "unix:///run/user/1002/podman/podman.sock"
        assert e.git_ref == "abc123"
        assert e.n_tasks == 10
        assert e.concurrency == 4
        assert e.split == "test"


class TestHarborExecutorSkillInjection:
    def test_skill_base64_in_env(self, tmp_path, monkeypatch) -> None:
        """When surface has a 'skill' prompt slot, SEARCHQA_SKILL_B64 is set."""
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        skill_content = "You are a search assistant."
        surface = Surface(prompt_slots={"skill": skill_content})
        e = HarborExecutor(harbor_script="run-harbor.sh")

        captured_env = {}

        def mock_run(cmd, cwd=None, env=None):
            captured_env.update(env or {})
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run):
            e.execute(tmp_path, surface)

        assert "SEARCHQA_SKILL_B64" in captured_env
        decoded = base64.b64decode(captured_env["SEARCHQA_SKILL_B64"]).decode()
        assert decoded == skill_content


class TestHarborExecutorTaskResultParsing:
    def test_parses_task_results_from_reward_json(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        reward = {
            "accuracy": 0.5,
            "tasks": [
                {"task_id": "q1", "reward": 1.0, "predicted": "Paris", "gold": "Paris", "question": "Capital of France?"},
                {"task_id": "q2", "reward": 0.0, "predicted": "Berlin", "gold": "London", "question": "Capital of UK?"},
            ],
        }
        (tmp_path / "reward.json").write_text(json.dumps(reward))

        e = HarborExecutor(harbor_script="run-harbor.sh")

        def mock_run(cmd, cwd=None, env=None):
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run):
            result = e.execute(tmp_path, Surface())

        assert len(result.task_results) == 2
        assert result.task_results[0].task_id == "q1"
        assert result.task_results[0].reward == 1.0
        assert result.task_results[1].task_id == "q2"
        assert result.task_results[1].reward == 0.0
        assert result.task_results[1].question == "Capital of UK?"

    def test_no_tasks_key_returns_empty(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        (tmp_path / "reward.json").write_text(json.dumps({"accuracy": 0.8}))

        e = HarborExecutor(harbor_script="run-harbor.sh")

        def mock_run(cmd, cwd=None, env=None):
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run):
            result = e.execute(tmp_path, Surface())

        assert result.task_results == []
