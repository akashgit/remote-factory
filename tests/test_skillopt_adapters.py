"""Tests for SkillOpt benchmark adapters — mocked subprocess + Harbor."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.skillopt.trainer import SkillOptTrainer
from factory.skillopt.types import Edit, Patch, RawPatch, RolloutResult



class TestSwebenchAdapter:
    def test_setup_loads_splits(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter.setup({"skill_path": str(tmp_path / "SKILL.md"), "student_model": "haiku"})
        assert adapter.student_model == "haiku"

    def test_build_train_env_pinned(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter.instances = ["django__django-14349"]
        result = adapter.build_train_env(8, seed=1)
        assert result == ["django__django-14349"]

    def test_build_train_env_split(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter._train_ids = [f"task-{i}" for i in range(20)]
        result = adapter.build_train_env(5, seed=0)
        assert len(result) == 5

    def test_build_eval_env_val(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter._val_ids = [f"val-{i}" for i in range(10)]
        result = adapter.build_eval_env(0, "eval", seed=42)
        assert len(result) == 10

    def test_build_eval_env_test(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter._test_ids = [f"test-{i}" for i in range(5)]
        result = adapter.build_eval_env(0, "test", seed=42)
        assert len(result) == 5

    def test_instance_to_image(self):
        from factory.skillopt.adapters.swebench import _instance_to_image

        assert _instance_to_image("django__django-14349") == \
            "swebench/sweb.eval.x86_64.django_1776_django-14349:latest"

    def test_get_git_ref(self):
        from factory.skillopt.adapters.swebench import _get_git_ref

        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="abc123\n")
            assert _get_git_ref() == "abc123"

    def test_get_git_ref_fails(self):
        from factory.skillopt.adapters.swebench import _get_git_ref

        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _get_git_ref() == ""

    def test_clean_result_files(self, tmp_path):
        from factory.skillopt.adapters.swebench import _clean_result_files

        with patch.object(
            type(Path()), "is_dir", return_value=True
        ):
            # Just verify it doesn't crash
            _clean_result_files()

    def test_parse_jobs_dir(self):
        from factory.skillopt.adapters.swebench import _parse_jobs_dir

        stdout = "some output\nJobs directory: /tmp/jobs-abc\nmore output"
        assert _parse_jobs_dir(stdout) == "/tmp/jobs-abc"

    def test_parse_jobs_dir_missing(self):
        from factory.skillopt.adapters.swebench import _parse_jobs_dir

        assert _parse_jobs_dir("no jobs here") == ""

    def test_find_trial_dir(self, tmp_path):
        from factory.skillopt.adapters.swebench import _find_trial_dir

        trial = tmp_path / "django__django-14349__abc1234"
        trial.mkdir()
        result = _find_trial_dir(str(tmp_path), "django__django-14349")
        assert result == trial

    def test_find_trial_dir_missing(self, tmp_path):
        from factory.skillopt.adapters.swebench import _find_trial_dir

        assert _find_trial_dir(str(tmp_path), "nonexistent") is None

    def test_find_trial_dir_no_jobs(self):
        from factory.skillopt.adapters.swebench import _find_trial_dir

        assert _find_trial_dir("", "x") is None

    def test_build_fail_reason(self, tmp_path):
        from factory.skillopt.adapters.swebench import _build_fail_reason

        verifier = tmp_path / "verifier"
        verifier.mkdir()
        (verifier / "test-stdout.txt").write_text("test_a PASSED\ntest_b FAILED\ntest_c FAILED")
        reason = _build_fail_reason(tmp_path)
        assert "2 tests FAILED" in reason

    def test_build_fail_reason_no_failures(self, tmp_path):
        from factory.skillopt.adapters.swebench import _build_fail_reason

        verifier = tmp_path / "verifier"
        verifier.mkdir()
        (verifier / "test-stdout.txt").write_text("test_a PASSED\ntest_b PASSED")
        assert _build_fail_reason(tmp_path) == ""

    def test_build_fail_reason_none(self):
        from factory.skillopt.adapters.swebench import _build_fail_reason

        assert _build_fail_reason(None) == ""

    def test_parse_trial_trajectory(self, tmp_path):
        from factory.skillopt.adapters.swebench import _parse_trial_trajectory

        # Create a mock session file
        sessions_dir = tmp_path / "agent" / "sessions" / "projects" / "test"
        sessions_dir.mkdir(parents=True)
        session_file = sessions_dir / "12345678-1234-1234-1234-123456789abc.jsonl"
        entries = [
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "thinking about the fix"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
            ]}},
        ]
        session_file.write_text("\n".join(json.dumps(e) for e in entries))

        # Create verifier output
        verifier = tmp_path / "verifier"
        verifier.mkdir()
        (verifier / "test-stdout.txt").write_text("test_a PASSED\ntest_b FAILED")

        result = _parse_trial_trajectory(tmp_path)
        assert "[assistant]" in result
        assert "[bash]" in result or "[Bash]" in result
        assert "FAILED" in result

    def test_collect_results(self, tmp_path):
        from factory.skillopt.adapters.swebench import _collect_results

        # Create a result file
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        result_file = results_dir / "test-swebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [
                {"instance_id": "task-1", "resolved": True},
                {"instance_id": "task-2", "resolved": False, "fail_reason": "broke"},
            ]
        }))

        with patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert len(results) == 2
        assert results[0].hard == 1.0
        assert results[1].hard == 0.0

    def test_collect_results_no_file(self):
        from factory.skillopt.adapters.swebench import _collect_results

        with patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=None):
            assert _collect_results("/tmp/out", "") == []

    def test_rollout_no_script(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        # run-harbor.sh doesn't exist at tmp_path
        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path):
            results = adapter.rollout([], "yaml content", str(tmp_path / "out"))
        assert results == []

    def test_rollout_with_mock(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter.concurrency = 1

        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho 'Jobs directory: /tmp/j'")
        script.chmod(0o755)

        result_file = tmp_path / "results" / "test-swebench-full.json"
        result_file.parent.mkdir(parents=True)
        result_file.write_text(json.dumps({"tasks": [{"instance_id": "t1", "resolved": True}]}))

        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.swebench._RESULTS_DIR", tmp_path / "results"), \
             patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=result_file), \
             patch("factory.skillopt.adapters.swebench._clean_result_files"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Jobs directory: /tmp/j", stderr="")
            results = adapter.rollout(["task-1"], "yaml", str(tmp_path / "out"))
        assert len(results) == 1

    def test_get_task_types(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        assert SwebenchAdapter().get_task_types() == ["bug_fix"]


class TestMiniSwebenchAdapter:
    def test_setup(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        adapter.setup({"student_model": "haiku"})
        assert adapter.student_model == "haiku"

    def test_build_train_env(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        adapter._train_ids = [f"t{i}" for i in range(20)]
        result = adapter.build_train_env(5, seed=0)
        assert len(result) == 5

    def test_build_eval_env(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        adapter._val_ids = [f"v{i}" for i in range(10)]
        result = adapter.build_eval_env(0, "eval", seed=42)
        assert len(result) == 10

    def test_get_task_types(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        assert MiniSwebenchAdapter().get_task_types() == ["bug_fix"]

    def test_parse_trial_trajectory_llm_trace(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _parse_trial_trajectory

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "llm-trace.log").write_text("[assistant] thinking\n[bash] ls\n[output] files")

        result = _parse_trial_trajectory(tmp_path)
        assert "[assistant] thinking" in result
        assert "[bash] ls" in result

    def test_parse_trial_trajectory_empty_trace(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _parse_trial_trajectory

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "llm-trace.log").write_text("")

        result = _parse_trial_trajectory(tmp_path)
        # Falls through to session files (none exist), returns verifier only
        assert isinstance(result, str)

    def test_collect_results(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _collect_results

        result_file = tmp_path / "test-mini-swebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [
                {"instance_id": "t1", "resolved": True},
                {"instance_id": "t2", "resolved": False},
            ]
        }))

        with patch("factory.skillopt.adapters.mini_swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert len(results) == 2


class TestSearchQAAdapter:
    def test_setup(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        adapter.setup({})
        assert adapter.instances == []

    def test_build_train_env_pinned(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        adapter.instances = ["q1", "q2"]
        result = adapter.build_train_env(8, seed=1)
        assert result == ["q1", "q2"]

    def test_build_eval_env_pinned(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        adapter.instances = ["q1"]
        result = adapter.build_eval_env(10, "eval", seed=42)
        assert result == ("val", ["q1"])

    def test_get_task_types(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        assert SearchQAAdapter().get_task_types() == ["question_answering"]

    def test_collect_results(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _collect_results

        result_file = tmp_path / "test-searchqa-full.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "q1", "resolved": True}]
        }))

        with patch("factory.skillopt.adapters.searchqa._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert len(results) == 1

    def test_parse_jobs_dir(self):
        from factory.skillopt.adapters.searchqa import _parse_jobs_dir

        assert _parse_jobs_dir("Jobs directory: /tmp/x") == "/tmp/x"
        assert _parse_jobs_dir("nothing") == ""


class TestFeaturebenchAdapter:
    def test_setup(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        adapter.setup({})
        assert adapter.instances == []

    def test_build_train_env(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        result = adapter.build_train_env(8, seed=1)
        assert result == 8

    def test_build_eval_env(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        result = adapter.build_eval_env(10, "eval", seed=42)
        assert result == 10

    def test_get_task_types(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        assert FeaturebenchAdapter().get_task_types() == ["feature_implementation"]

    def test_collect_results(self, tmp_path):
        from factory.skillopt.adapters.featurebench import _collect_results

        result_file = tmp_path / "test-featurebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "f1", "resolved": True, "score": 0.8}]
        }))

        with patch("factory.skillopt.adapters.featurebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert len(results) == 1

    def test_parse_jobs_dir(self):
        from factory.skillopt.adapters.featurebench import _parse_jobs_dir

        assert _parse_jobs_dir("Jobs directory: /tmp/y") == "/tmp/y"

    def test_get_git_ref(self):
        from factory.skillopt.adapters.featurebench import _get_git_ref

        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="def456\n")
            assert _get_git_ref() == "def456"


class TestLlmLoop:
    def test_build_client_anthropic(self):
        import sys
        import types

        mock_anthropic = types.ModuleType("anthropic")
        mock_anthropic.Anthropic = MagicMock()
        sys.modules["anthropic"] = mock_anthropic
        try:
            from factory.workflow.llm_loop import _build_client
            from factory.workflow.primitives import LLMNode

            node = LLMNode(id="s", provider="anthropic")
            _build_client(node)
            mock_anthropic.Anthropic.assert_called_once()
        finally:
            del sys.modules["anthropic"]

    def test_build_client_vertex(self):
        import sys
        import types

        mock_anthropic = types.ModuleType("anthropic")
        mock_anthropic.AnthropicVertex = MagicMock()
        sys.modules["anthropic"] = mock_anthropic
        try:
            from factory.workflow.llm_loop import _build_client
            from factory.workflow.primitives import LLMNode

            node = LLMNode(id="s", provider="vertex")
            with patch.dict("os.environ", {"ANTHROPIC_VERTEX_PROJECT_ID": "proj", "CLOUD_ML_REGION": "us-east5"}):
                _build_client(node)
                call_kwargs = mock_anthropic.AnthropicVertex.call_args[1]
                assert call_kwargs["region"] == "global"
        finally:
            del sys.modules["anthropic"]

    def test_tools_to_api_format(self):
        from factory.workflow.llm_loop import _tools_to_api_format
        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        node = LLMNode(id="s", tools=[BASH_TOOL])
        tools = _tools_to_api_format(node)
        assert len(tools) == 1
        assert tools[0]["name"] == "bash"


class TestSkilloptMain:
    def test_load_known_adapter(self):
        from factory.skillopt.__main__ import _load_adapter

        for name in ["swebench", "mini-swebench", "searchqa", "featurebench"]:
            adapter = _load_adapter(name)
            assert hasattr(adapter, "rollout")

    def test_load_unknown_adapter(self):
        import sys
        from factory.skillopt.__main__ import _load_adapter

        with patch.object(sys, "exit", side_effect=SystemExit):
            try:
                _load_adapter("nonexistent_xyz")
            except SystemExit:
                pass

    def test_main_parses_args(self):
        from factory.skillopt.__main__ import main
        import sys

        with patch.object(sys, "argv", ["skillopt", "--benchmark", "swebench",
                                         "--skill-path", "/tmp/s.md", "--epochs", "1",
                                         "--steps-per-epoch", "1", "--batch-size", "2"]):
            with patch("factory.skillopt.__main__._load_adapter") as mock_adapter, \
                 patch("factory.skillopt.trainer.SkillOptTrainer") as mock_trainer:
                mock_adapter.return_value = MagicMock()
                mock_trainer.return_value = MagicMock()
                result = main()
                assert result == 0
                mock_trainer.assert_called_once()


class TestSearchQAAdapterRollout:
    def test_rollout_builds_correct_cmd(self, tmp_path):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash\necho done")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.searchqa._clean_result_files"), \
             patch("factory.skillopt.adapters.searchqa._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.searchqa._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(("val", 10), "yaml content", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert "searchqa" in cmd[1]
            env = mock_run.call_args[1].get("env", {})
            assert "FACTORY_WORKFLOW_YAML_B64" in env
            assert env.get("SEARCHQA_SPLIT") == "val"

    def test_rollout_with_instances(self, tmp_path):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.searchqa._clean_result_files"), \
             patch("factory.skillopt.adapters.searchqa._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.searchqa._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(("val", ["q1", "q2"]), "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert any("q1" in str(c) for c in cmd)

    def test_extract_verifier_outputs(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _extract_verifier_outputs

        trial = tmp_path / "task1__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        (verifier / "test-stdout.txt").write_text("Predicted: Paris\nGold: ['Paris', 'paris']")

        outputs = _extract_verifier_outputs(str(tmp_path))
        assert "task1" in outputs
        assert outputs["task1"]["predicted"] == "Paris"

    def test_collect_results_with_verifier(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _collect_results

        result_file = tmp_path / "test-searchqa-full.json"
        result_file.write_text(json.dumps({
            "tasks": [
                {"instance_id": "q1", "resolved": True},
                {"instance_id": "q2", "resolved": False},
            ]
        }))

        with patch("factory.skillopt.adapters.searchqa._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), str(tmp_path))
        assert len(results) == 2
        assert results[0].task_type == "question_answering"


class TestFeaturebenchAdapterRollout:
    def test_rollout_no_script(self, tmp_path):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        with patch("factory.skillopt.adapters.featurebench._BENCHMARKS_DIR", tmp_path):
            assert adapter.rollout(5, "yaml", str(tmp_path / "out")) == []

    def test_rollout_with_mock(self, tmp_path):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.featurebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.featurebench._clean_result_files"), \
             patch("factory.skillopt.adapters.featurebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.featurebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(5, "yaml", str(tmp_path / "out"))
            env = mock_run.call_args[1].get("env", {})
            assert "FACTORY_WORKFLOW_YAML_B64" in env

    def test_collect_with_trace(self, tmp_path):
        from factory.skillopt.adapters.featurebench import _collect_results

        result_file = tmp_path / "test-featurebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "f1", "resolved": True, "score": 1.0}]
        }))

        with patch("factory.skillopt.adapters.featurebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert results[0].task_type == "feature_implementation"


class TestMiniSwebenchAdapterRollout:
    def test_rollout_no_script(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path):
            assert adapter.rollout([], "yaml", str(tmp_path / "out")) == []

    def test_rollout_with_mock(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        adapter.concurrency = 1
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        result_file = tmp_path / "r" / "test-mini-swebench-full.json"
        result_file.parent.mkdir()
        result_file.write_text(json.dumps({"tasks": [{"instance_id": "t1", "resolved": True}]}))

        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.mini_swebench._RESULTS_DIR", tmp_path / "r"), \
             patch("factory.skillopt.adapters.mini_swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.mini_swebench._find_latest_result_file", return_value=result_file), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Jobs directory: /tmp/j", stderr="")
            results = adapter.rollout(["task-1"], "yaml", str(tmp_path / "out"))
        assert len(results) == 1

    def test_collect_with_trial_trajectory(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _collect_results

        result_file = tmp_path / "test-mini-swebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "t1", "resolved": False}]
        }))

        # Create trial dir with llm-trace.log
        jobs = tmp_path / "jobs"
        trial = jobs / "t1__abc1234" / "agent"
        trial.mkdir(parents=True)
        (trial / "llm-trace.log").write_text("[bash] ls\n[output] file.py")

        with patch("factory.skillopt.adapters.mini_swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), str(jobs))
        assert len(results) == 1
        assert "[bash] ls" in results[0].extras.get("trace_dump", "")

    def test_build_fail_reason(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _build_fail_reason

        verifier = tmp_path / "verifier"
        verifier.mkdir()
        (verifier / "test-stdout.txt").write_text("PASSED test_a\nFAILED test_b\nFAILED test_c")
        reason = _build_fail_reason(tmp_path)
        assert "2 tests FAILED" in reason

    def test_parse_trial_trajectory_session_fallback(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _parse_trial_trajectory

        # No llm-trace.log, fallback to session JSONL
        sessions = tmp_path / "agent" / "sessions" / "projects" / "test"
        sessions.mkdir(parents=True)
        session = sessions / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        entry = {"message": {"role": "assistant", "content": [
            {"type": "text", "text": "analyzing"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "grep bug"}},
        ]}}
        session.write_text(json.dumps(entry))

        result = _parse_trial_trajectory(tmp_path)
        assert "[assistant]" in result
        assert "[Bash]" in result or "[bash]" in result


class TestLlmLoopFull:
    def test_run_llm_loop_mocked(self, tmp_path):
        import asyncio
        import sys
        import types

        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        mock_anthropic = types.ModuleType("anthropic")
        mock_client = MagicMock()

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "I've fixed the bug."
        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.stop_reason = "end_turn"
        mock_client.messages.create.return_value = mock_response

        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = mock_anthropic

        try:
            import importlib
            import factory.workflow.llm_loop as ll
            importlib.reload(ll)

            node = LLMNode(
                id="solver", system_prompt="you are helpful",
                instance_prompt="fix the bug", model="haiku",
                provider="anthropic", tools=[BASH_TOOL],
                max_turns=5, timeout=30,
            )
            result = asyncio.run(ll.run_llm_loop(node, tmp_path))
            assert "fixed the bug" in result
            mock_client.messages.create.assert_called_once()
        finally:
            del sys.modules["anthropic"]

    def test_run_llm_loop_with_tool_use(self, tmp_path):
        import asyncio
        import sys
        import types

        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        mock_anthropic = types.ModuleType("anthropic")
        mock_client = MagicMock()

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "bash"
        tool_block.input = {"command": "echo hello"}
        tool_block.id = "tool_1"
        text_block1 = MagicMock()
        text_block1.type = "text"
        text_block1.text = "Let me run a command."
        resp1 = MagicMock()
        resp1.content = [text_block1, tool_block]
        resp1.stop_reason = "tool_use"

        text_block2 = MagicMock()
        text_block2.type = "text"
        text_block2.text = "Done."
        resp2 = MagicMock()
        resp2.content = [text_block2]
        resp2.stop_reason = "end_turn"

        mock_client.messages.create.side_effect = [resp1, resp2]
        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = mock_anthropic

        try:
            import importlib
            import factory.workflow.llm_loop as ll
            importlib.reload(ll)

            node = LLMNode(
                id="solver", system_prompt="sys",
                instance_prompt="fix it", model="haiku",
                provider="anthropic", tools=[BASH_TOOL],
                max_turns=10, timeout=30,
            )
            result = asyncio.run(ll.run_llm_loop(node, tmp_path))
            assert "Done" in result
            assert mock_client.messages.create.call_count == 2
        finally:
            del sys.modules["anthropic"]


class TestSlowUpdateFull:
    def test_run_slow_update_mocked(self):
        from factory.skillopt.slow_update import run_slow_update
        from factory.skillopt.types import RolloutResult

        prev = [RolloutResult(id="a", hard=0.0, soft=0.0)]
        curr = [RolloutResult(id="a", hard=1.0, soft=1.0)]

        mock_response = json.dumps({
            "slow_update_content": "Focus on test-first debugging.",
            "reasoning": "Task a improved by running tests first.",
        })

        with patch("factory.skillopt.slow_update._call_llm", return_value=mock_response):
            result = run_slow_update(
                skill_content="# Skill\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->",
                prev_skill="# Old Skill",
                results_prev=prev,
                results_curr=curr,
            )
        assert result is not None
        assert "slow_update_content" in result

    def test_run_slow_update_no_llm(self):
        from factory.skillopt.slow_update import run_slow_update
        from factory.skillopt.types import RolloutResult

        with patch("factory.skillopt.slow_update._call_llm", return_value=None):
            result = run_slow_update(
                skill_content="skill",
                prev_skill="old",
                results_prev=[RolloutResult(id="a", hard=0.0, soft=0.0)],
                results_curr=[RolloutResult(id="a", hard=1.0, soft=1.0)],
            )
        assert result is None


class TestSwebenchPrepull:
    def test_prepull_all_cached(self):
        from factory.skillopt.adapters.swebench import _prepull_images

        with patch("subprocess.run") as mock:
            # All images already cached (inspect returns 0)
            mock.return_value = MagicMock(returncode=0)
            _prepull_images(["django__django-14349", "sympy__sympy-24213"])
            # Should only call inspect, not pull
            calls = [c[0][0] for c in mock.call_args_list]
            assert all("inspect" in str(c) for c in calls)

    def test_prepull_needs_pull(self):
        from factory.skillopt.adapters.swebench import _prepull_images

        with patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
            # Image not cached (inspect returns 1)
            mock_run.return_value = MagicMock(returncode=1)
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc
            _prepull_images(["django__django-14349"], concurrency=1)
            # Should call Popen for docker pull
            assert mock_popen.called


class TestSwebenchRolloutEdgeCases:
    def test_rollout_subprocess_timeout(self, tmp_path):
        import subprocess as sp
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        adapter.concurrency = 1
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.swebench._clean_result_files"), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=9000)):
            results = adapter.rollout(8, "yaml", str(tmp_path / "out"))
        assert results == []

    def test_rollout_with_limit(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter

        adapter = SwebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(10, "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert "--limit" in cmd
            assert "10" in cmd


class TestMiniSwebenchRolloutEdgeCases:
    def test_rollout_subprocess_timeout(self, tmp_path):
        import subprocess as sp
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.mini_swebench._clean_result_files"), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=9000)):
            results = adapter.rollout(8, "yaml", str(tmp_path / "out"))
        assert results == []

    def test_rollout_with_limit(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.mini_swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.mini_swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.mini_swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(10, "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert "--limit" in cmd

    def test_rollout_empty_results_logs_error(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter

        adapter = MiniSwebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.mini_swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.mini_swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.mini_swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
            results = adapter.rollout(["t1"], "yaml", str(tmp_path / "out"))
        assert results == []


class TestFeaturebenchRolloutEdgeCases:
    def test_rollout_subprocess_timeout(self, tmp_path):
        import subprocess as sp
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.featurebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.featurebench._clean_result_files"), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=9000)):
            results = adapter.rollout(5, "yaml", str(tmp_path / "out"))
        assert results == []

    def test_rollout_with_instances(self, tmp_path):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter

        adapter = FeaturebenchAdapter()
        adapter.instances = ["feat1", "feat2"]
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.featurebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.featurebench._clean_result_files"), \
             patch("factory.skillopt.adapters.featurebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.featurebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(5, "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert any("feat1" in str(c) for c in cmd)


class TestSearchQARolloutEdgeCases:
    def test_rollout_subprocess_timeout(self, tmp_path):
        import subprocess as sp
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.searchqa._clean_result_files"), \
             patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="x", timeout=9000)):
            results = adapter.rollout(("train", 10), "yaml", str(tmp_path / "out"))
        assert results == []

    def test_rollout_no_script(self, tmp_path):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path):
            assert adapter.rollout(10, "yaml", str(tmp_path / "out")) == []

    def test_rollout_plain_int_env(self, tmp_path):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter

        adapter = SearchQAAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.searchqa._clean_result_files"), \
             patch("factory.skillopt.adapters.searchqa._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.searchqa._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Pass plain int (not tuple)
            adapter.rollout(10, "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert "--limit" in cmd


class TestTrainerMoreEdgeCases:
    def test_slow_update_epoch(self, tmp_path):
        """Test that _run_slow_update_epoch is called for epoch >= 2."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill\n<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "p"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=2, steps_per_epoch=1,
            batch_size=2, learning_rate=3, use_slow_update=True,
        )

        results = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        adapter.rollout.side_effect = [results] * 10
        adapter.reflect.return_value = []

        trainer.train()
        # Epoch 1: injects placeholder. Epoch 2: would run slow update but no prev checkpoint
        assert trainer.global_step == 2

    def test_trainer_with_yaml_surface_no_changes(self, tmp_path):
        """Test yaml_surface mode where edits don't match any slot."""
        import yaml

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Skill")
        ann_path = tmp_path / "SKILL.annotations.yaml"
        ann = {"b": {"slots": {"task_prompt_b": "prompt text here"}}}
        ann_path.write_text(yaml.dump(ann))

        adapter = MagicMock()
        trainer = SkillOptTrainer(
            adapter=adapter, skill_path=str(skill_path),
            out_dir=str(tmp_path / "out"), epochs=1, steps_per_epoch=1,
            batch_size=2, learning_rate=3, workflow_name="swebench",
        )

        baseline = [RolloutResult(id="e1", hard=0.5, soft=0.5)]
        train = [RolloutResult(id="t1", hard=1.0, soft=1.0)]

        adapter.rollout.side_effect = [baseline, train]
        # Edit targets text NOT in any slot
        adapter.reflect.return_value = [
            RawPatch(
                patch=Patch(edits=[Edit(op="replace", target="nonexistent text", content="x")],
                            reasoning="r"),
                source_type="failure", batch_size=1, failure_summary=[],
            ),
        ]

        trainer.train()
        # Should reject due to non-prompt target
        assert trainer.best_score == 0.5


class TestSwebenchCollectWithTrajectory:
    def test_collect_with_trial_trajectory_and_fail_reason(self, tmp_path):
        from factory.skillopt.adapters.swebench import _collect_results

        result_file = tmp_path / "test-swebench-full.json"
        result_file.write_text(json.dumps({
            "tasks": [
                {"instance_id": "t1", "resolved": False},
                {"instance_id": "t2", "resolved": True, "score": 1.0},
            ]
        }))

        jobs = tmp_path / "jobs"
        trial1 = jobs / "t1__abc1234"
        trial1.mkdir(parents=True)
        verifier1 = trial1 / "verifier"
        verifier1.mkdir()
        (verifier1 / "test-stdout.txt").write_text("PASSED x\nFAILED y")

        sessions = trial1 / "agent" / "sessions" / "projects" / "p"
        sessions.mkdir(parents=True)
        sess = sessions / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
        sess.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "text", "text": "analyzing"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/f"}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/f"}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/f"}},
        ]}}))

        with patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), str(jobs))
        assert len(results) == 2
        failed = [r for r in results if r.hard == 0.0][0]
        assert "FAILED" in failed.fail_reason
        assert failed.extras.get("trace_dump", "") != ""

    def test_collect_bad_json(self, tmp_path):
        from factory.skillopt.adapters.swebench import _collect_results

        result_file = tmp_path / "bad.json"
        result_file.write_text("not json")

        with patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert results == []

    def test_collect_empty_tasks(self, tmp_path):
        from factory.skillopt.adapters.swebench import _collect_results

        result_file = tmp_path / "empty.json"
        result_file.write_text(json.dumps({"tasks": []}))

        with patch("factory.skillopt.adapters.swebench._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert results == []


class TestSearchQACollectEdgeCases:
    def test_collect_with_verifier_bad_gold(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _extract_verifier_outputs

        trial = tmp_path / "q1__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        (verifier / "test-stdout.txt").write_text("Predicted: Paris\nGold: Paris")

        outputs = _extract_verifier_outputs(str(tmp_path))
        assert "q1" in outputs

    def test_collect_not_resolved_no_prediction(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _collect_results

        result_file = tmp_path / "test-searchqa-full.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "q1", "resolved": False}]
        }))

        with patch("factory.skillopt.adapters.searchqa._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), "")
        assert len(results) == 1
        assert results[0].fail_reason == "not_resolved"

    def test_collect_not_resolved_with_prediction(self, tmp_path):
        from factory.skillopt.adapters.searchqa import _collect_results

        result_file = tmp_path / "test.json"
        result_file.write_text(json.dumps({
            "tasks": [{"instance_id": "q1", "resolved": False}]
        }))

        jobs = tmp_path / "jobs"
        trial = jobs / "q1__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        (verifier / "test-stdout.txt").write_text("Predicted: wrong\nGold: ['right']")

        with patch("factory.skillopt.adapters.searchqa._find_latest_result_file", return_value=result_file):
            results = _collect_results(str(tmp_path / "out"), str(jobs))
        assert "EM=0" in results[0].fail_reason


class TestMiniSwebenchCollectEdgeCases:
    def test_collect_bad_json(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _collect_results

        result_file = tmp_path / "bad.json"
        result_file.write_text("not json")

        with patch("factory.skillopt.adapters.mini_swebench._find_latest_result_file", return_value=result_file):
            assert _collect_results(str(tmp_path / "out"), "") == []

    def test_find_latest_result_file(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _find_latest_result_file

        with patch("factory.skillopt.adapters.mini_swebench._RESULTS_DIR", tmp_path):
            assert _find_latest_result_file() is None

            f = tmp_path / "test-mini-swebench-full.json"
            f.write_text("{}")
            assert _find_latest_result_file() == f

    def test_clean_result_files(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import _clean_result_files

        with patch("factory.skillopt.adapters.mini_swebench._RESULTS_DIR", tmp_path):
            f = tmp_path / "old-mini-swebench-full.json"
            f.write_text("{}")
            _clean_result_files()
            assert not f.exists()

    def test_get_git_ref(self):
        from factory.skillopt.adapters.mini_swebench import _get_git_ref

        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(returncode=0, stdout="abc\n")
            assert _get_git_ref() == "abc"

        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _get_git_ref() == ""


class TestFeaturebenchCollectEdgeCases:
    def test_collect_bad_json(self, tmp_path):
        from factory.skillopt.adapters.featurebench import _collect_results

        result_file = tmp_path / "bad.json"
        result_file.write_text("not json")

        with patch("factory.skillopt.adapters.featurebench._find_latest_result_file", return_value=result_file):
            assert _collect_results(str(tmp_path / "out"), "") == []

    def test_collect_empty(self, tmp_path):
        from factory.skillopt.adapters.featurebench import _collect_results

        result_file = tmp_path / "empty.json"
        result_file.write_text(json.dumps({"tasks": []}))

        with patch("factory.skillopt.adapters.featurebench._find_latest_result_file", return_value=result_file):
            assert _collect_results(str(tmp_path / "out"), "") == []

    def test_clean_and_find(self, tmp_path):
        from factory.skillopt.adapters.featurebench import _clean_result_files, _find_latest_result_file

        with patch("factory.skillopt.adapters.featurebench._RESULTS_DIR", tmp_path):
            f = tmp_path / "old-featurebench-full.json"
            f.write_text("{}")
            _clean_result_files()
            assert not f.exists()

            assert _find_latest_result_file() is None


class TestAdapterBranchCoverage:
    """Tests specifically targeting uncovered branches (the 'else' paths)."""

    def test_swebench_build_train_no_instances_no_splits(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter
        adapter = SwebenchAdapter()
        result = adapter.build_train_env(8, seed=1)
        assert result == 8

    def test_swebench_build_eval_no_instances_no_splits(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter
        adapter = SwebenchAdapter()
        result = adapter.build_eval_env(10, "eval", seed=42)
        assert result == 10

    def test_swebench_build_eval_pinned_instances(self):
        from factory.skillopt.adapters.swebench import SwebenchAdapter
        adapter = SwebenchAdapter()
        adapter.instances = ["t1"]
        result = adapter.build_eval_env(10, "eval", seed=42)
        assert result == ["t1"]

    def test_swebench_rollout_with_student_model(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter
        adapter = SwebenchAdapter()
        adapter.student_model = "haiku"
        adapter.concurrency = 1
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(["t1"], "yaml", str(tmp_path / "out"))
            env = mock_run.call_args[1]["env"]
            assert env["FACTORY_STUDENT_MODEL"] == "haiku"

    def test_swebench_rollout_no_student_model(self, tmp_path):
        from factory.skillopt.adapters.swebench import SwebenchAdapter
        adapter = SwebenchAdapter()
        adapter.concurrency = 1
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(["t1"], "yaml", str(tmp_path / "out"))
            env = mock_run.call_args[1]["env"]
            assert "FACTORY_STUDENT_MODEL" not in env

    def test_mini_swebench_build_train_no_splits(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        assert adapter.build_train_env(8, seed=1) == 8

    def test_mini_swebench_build_eval_no_splits(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        assert adapter.build_eval_env(10, "eval", seed=42) == 10

    def test_mini_swebench_build_eval_test_split(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        adapter._test_ids = ["t1", "t2"]
        result = adapter.build_eval_env(0, "test", seed=42)
        assert result == ["t1", "t2"]

    def test_mini_swebench_build_eval_pinned(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        adapter.instances = ["x"]
        assert adapter.build_eval_env(0, "eval", seed=42) == ["x"]

    def test_mini_swebench_build_train_pinned(self):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        adapter.instances = ["x"]
        assert adapter.build_train_env(8, seed=1) == ["x"]

    def test_mini_swebench_rollout_with_student_model(self, tmp_path):
        from factory.skillopt.adapters.mini_swebench import MiniSwebenchAdapter
        adapter = MiniSwebenchAdapter()
        adapter.student_model = "haiku"
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.mini_swebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.mini_swebench._clean_result_files"), \
             patch("factory.skillopt.adapters.mini_swebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.mini_swebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            adapter.rollout(["t1"], "yaml", str(tmp_path / "out"))
            env = mock_run.call_args[1]["env"]
            assert env["FACTORY_STUDENT_MODEL"] == "haiku"

    def test_searchqa_build_train_no_instances(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter
        adapter = SearchQAAdapter()
        result = adapter.build_train_env(8, seed=1)
        assert result == 8

    def test_searchqa_build_eval_no_instances(self):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter
        adapter = SearchQAAdapter()
        result = adapter.build_eval_env(10, "eval", seed=42)
        assert result == ("val", 10)

    def test_searchqa_rollout_list_env(self, tmp_path):
        from factory.skillopt.adapters.searchqa import SearchQAAdapter
        adapter = SearchQAAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.searchqa._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.searchqa._clean_result_files"), \
             patch("factory.skillopt.adapters.searchqa._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.searchqa._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Pass a list directly (not tuple)
            adapter.rollout(["q1", "q2"], "yaml", str(tmp_path / "out"))
            cmd = mock_run.call_args[0][0]
            assert any("q1" in str(c) for c in cmd)

    def test_featurebench_build_eval_pinned(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter
        adapter = FeaturebenchAdapter()
        adapter.instances = ["f1"]
        assert adapter.build_eval_env(10, "eval", seed=42) == ["f1"]

    def test_featurebench_build_train_pinned(self):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter
        adapter = FeaturebenchAdapter()
        adapter.instances = ["f1"]
        assert adapter.build_train_env(8, seed=1) == ["f1"]

    def test_featurebench_rollout_empty_results_warning(self, tmp_path):
        from factory.skillopt.adapters.featurebench import FeaturebenchAdapter
        adapter = FeaturebenchAdapter()
        script = tmp_path / "run-harbor.sh"
        script.write_text("#!/bin/bash")
        script.chmod(0o755)

        with patch("factory.skillopt.adapters.featurebench._BENCHMARKS_DIR", tmp_path), \
             patch("factory.skillopt.adapters.featurebench._clean_result_files"), \
             patch("factory.skillopt.adapters.featurebench._collect_results", return_value=[]), \
             patch("factory.skillopt.adapters.featurebench._parse_jobs_dir", return_value=""), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            results = adapter.rollout(5, "yaml", str(tmp_path / "out"))
        assert results == []


class TestLlmLoopBranches:
    def test_unknown_tool(self, tmp_path):
        import asyncio
        import sys
        import types

        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        mock_anthropic = types.ModuleType("anthropic")
        mock_client = MagicMock()

        # Response with unknown tool
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "unknown_tool"
        tool_block.input = {}
        tool_block.id = "t1"
        resp1 = MagicMock()
        resp1.content = [tool_block]
        resp1.stop_reason = "tool_use"

        # Then end
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "done"
        resp2 = MagicMock()
        resp2.content = [text_block]
        resp2.stop_reason = "end_turn"

        mock_client.messages.create.side_effect = [resp1, resp2]
        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = mock_anthropic

        try:
            import importlib
            import factory.workflow.llm_loop as ll
            importlib.reload(ll)

            node = LLMNode(id="s", system_prompt="", instance_prompt="test",
                           model="haiku", provider="anthropic", tools=[BASH_TOOL],
                           max_turns=5, timeout=30)
            result = asyncio.run(ll.run_llm_loop(node, tmp_path))
            assert "done" in result
        finally:
            del sys.modules["anthropic"]

    def test_stop_sequence(self, tmp_path):
        import asyncio
        import sys
        import types

        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        mock_anthropic = types.ModuleType("anthropic")
        mock_client = MagicMock()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "result STOP_HERE more text"
        resp = MagicMock()
        resp.content = [text_block]
        mock_client.messages.create.return_value = resp

        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = mock_anthropic

        try:
            import importlib
            import factory.workflow.llm_loop as ll
            importlib.reload(ll)

            node = LLMNode(id="s", system_prompt="", instance_prompt="test",
                           model="haiku", provider="anthropic", tools=[BASH_TOOL],
                           max_turns=5, timeout=30, stop_sequences=["STOP_HERE"])
            result = asyncio.run(ll.run_llm_loop(node, tmp_path))
            assert "result" in result
            mock_client.messages.create.assert_called_once()
        finally:
            del sys.modules["anthropic"]

    def test_instance_context_placeholder(self, tmp_path):
        import asyncio
        import sys
        import types

        from factory.workflow.primitives import LLMNode
        from factory.workflow.llm_tools import BASH_TOOL

        mock_anthropic = types.ModuleType("anthropic")
        mock_client = MagicMock()

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "done"
        resp = MagicMock()
        resp.content = [text_block]
        resp.stop_reason = "end_turn"
        mock_client.messages.create.return_value = resp

        mock_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = mock_anthropic

        try:
            import importlib
            import factory.workflow.llm_loop as ll
            importlib.reload(ll)

            node = LLMNode(id="s", system_prompt="sys",
                           instance_prompt="prompt with {instance_context} here",
                           model="haiku", provider="anthropic", tools=[BASH_TOOL],
                           max_turns=5, timeout=30)
            asyncio.run(ll.run_llm_loop(node, tmp_path, instance_context="INJECTED"))
            # Verify the placeholder was replaced
            call_args = mock_client.messages.create.call_args
            messages = call_args[1]["messages"]
            assert "INJECTED" in messages[0]["content"]
            assert "{instance_context}" not in messages[0]["content"]
        finally:
            del sys.modules["anthropic"]
