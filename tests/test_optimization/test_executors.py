"""Tests for factory.optimization.executors — protocol conformance and basic behavior."""

from __future__ import annotations

import base64
import json

from factory.optimization.executors import (
    FactoryCeoExecutor,
    HarborExecutor,
    WorkflowRunExecutor,
)
from factory.optimization.executors.harbor import (
    _parse_trial_results,
    _strip_harbor_suffix,
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

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
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

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
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

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
            result = e.execute(tmp_path, Surface())

        assert result.task_results == []


class TestHarborExecutorCommand:
    """Tests for full command construction with arguments."""

    def test_default_command_args(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        e = HarborExecutor(harbor_script="run-harbor.sh")
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None):
            captured_cmd.extend(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
            e.execute(tmp_path, Surface())

        assert captured_cmd[1] == "searchqa"
        assert "--all" in captured_cmd
        assert "--limit" in captured_cmd
        assert captured_cmd[captured_cmd.index("--limit") + 1] == "5"
        assert "--concurrency" in captured_cmd
        assert captured_cmd[captured_cmd.index("--concurrency") + 1] == "2"
        assert "--timeout" in captured_cmd
        assert captured_cmd[captured_cmd.index("--timeout") + 1] == "120"
        assert "--solver" in captured_cmd
        assert captured_cmd[captured_cmd.index("--solver") + 1] == "factory"
        assert "--preserve" in captured_cmd

    def test_custom_n_tasks_and_concurrency(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        e = HarborExecutor(harbor_script="run-harbor.sh", n_tasks=20, concurrency=8)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None):
            captured_cmd.extend(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
            e.execute(tmp_path, Surface())

        assert captured_cmd[captured_cmd.index("--limit") + 1] == "20"
        assert captured_cmd[captured_cmd.index("--concurrency") + 1] == "8"

    def test_split_flag_included(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        e = HarborExecutor(harbor_script="run-harbor.sh", split="dev")
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None):
            captured_cmd.extend(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
            e.execute(tmp_path, Surface())

        assert "--split" in captured_cmd
        assert captured_cmd[captured_cmd.index("--split") + 1] == "dev"

    def test_no_split_omits_flag(self, tmp_path) -> None:
        from unittest.mock import patch as mock_patch
        from factory.optimization.surface import Surface

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho ok")
        script.chmod(0o755)

        e = HarborExecutor(harbor_script="run-harbor.sh")
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None):
            captured_cmd.extend(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        with mock_patch("factory.optimization.executors.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.executors.harbor._find_latest_jobs_dir", return_value=""):
            e.execute(tmp_path, Surface())

        assert "--split" not in captured_cmd


class TestStripHarborSuffix:
    """Tests for _strip_harbor_suffix helper."""

    def test_strips_7char_suffix(self) -> None:
        assert _strip_harbor_suffix("my-task__abc1234") == "my-task"

    def test_preserves_name_without_suffix(self) -> None:
        assert _strip_harbor_suffix("my-task") == "my-task"

    def test_preserves_single_underscore(self) -> None:
        assert _strip_harbor_suffix("my_task") == "my_task"

    def test_handles_complex_task_id(self) -> None:
        assert _strip_harbor_suffix("nq-train-12345__xf9g2h1") == "nq-train-12345"

    def test_short_name_not_stripped(self) -> None:
        assert _strip_harbor_suffix("a__b1c2d3e") == "a"

    def test_too_short_returns_unchanged(self) -> None:
        assert _strip_harbor_suffix("__abc1234") == "__abc1234"


class TestParseTrialResults:
    """Tests for _parse_trial_results — parsing per-task verifier outputs."""

    def test_parses_reward_and_stdout(self, tmp_path) -> None:
        trial = tmp_path / "task-001__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        verifier.joinpath("reward.json").write_text(json.dumps({"reward": 1.0}))
        verifier.joinpath("test-stdout.txt").write_text(
            "Running test...\nPredicted: Paris\nGold: ['Paris']\nPASS\n"
        )

        results = _parse_trial_results(tmp_path)
        assert len(results) == 1
        assert results[0].task_id == "task-001"
        assert results[0].reward == 1.0
        assert results[0].predicted == "Paris"
        assert results[0].gold == "['Paris']"
        assert results[0].question == ""

    def test_missing_reward_defaults_zero(self, tmp_path) -> None:
        trial = tmp_path / "task-002__xyz5678"
        trial.mkdir(parents=True)

        results = _parse_trial_results(tmp_path)
        assert len(results) == 1
        assert results[0].task_id == "task-002"
        assert results[0].reward == 0.0

    def test_missing_stdout_empty_strings(self, tmp_path) -> None:
        trial = tmp_path / "task-003__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        verifier.joinpath("reward.json").write_text(json.dumps({"reward": 0.5}))

        results = _parse_trial_results(tmp_path)
        assert len(results) == 1
        assert results[0].predicted == ""
        assert results[0].gold == ""

    def test_multiple_trials(self, tmp_path) -> None:
        for i, (task, reward) in enumerate([("q1", 1.0), ("q2", 0.0), ("q3", 0.5)]):
            trial = tmp_path / f"{task}__abcdef{i}"
            verifier = trial / "verifier"
            verifier.mkdir(parents=True)
            verifier.joinpath("reward.json").write_text(json.dumps({"reward": reward}))

        results = _parse_trial_results(tmp_path)
        assert len(results) == 3
        rewards = {r.task_id: r.reward for r in results}
        assert rewards["q1"] == 1.0
        assert rewards["q2"] == 0.0
        assert rewards["q3"] == 0.5

    def test_empty_dir_returns_empty(self, tmp_path) -> None:
        assert _parse_trial_results(tmp_path) == []

    def test_nonexistent_dir_returns_empty(self, tmp_path) -> None:
        assert _parse_trial_results(tmp_path / "nonexistent") == []

    def test_invalid_reward_json(self, tmp_path) -> None:
        trial = tmp_path / "task-bad__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        verifier.joinpath("reward.json").write_text("not json")

        results = _parse_trial_results(tmp_path)
        assert len(results) == 1
        assert results[0].reward == 0.0
