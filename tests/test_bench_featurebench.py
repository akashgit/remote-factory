"""Tests for benchmarks/featurebench-bench/bench.py.

Uses mocks — does NOT require Docker, HuggingFace, or real FeatureBench.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add benchmarks dir to path so we can import bench
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks" / "featurebench-bench"))

import bench


class TestLoadTaskIdsFromSplit:
    def test_loads_from_jsonl(self, tmp_path: Path) -> None:
        split_file = tmp_path / "val.jsonl"
        split_file.write_text(
            '{"instance_id": "task_a", "repo": "foo", "level": 1}\n'
            '{"instance_id": "task_b", "repo": "bar", "level": 2}\n'
        )
        ids = bench.load_task_ids_from_split(split_file)
        assert ids == ["task_a", "task_b"]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        split_file = tmp_path / "test.jsonl"
        split_file.write_text(
            '{"instance_id": "x"}\n'
            "\n"
            '{"instance_id": "y"}\n'
        )
        ids = bench.load_task_ids_from_split(split_file)
        assert ids == ["x", "y"]

    def test_raises_on_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            bench.load_task_ids_from_split("/nonexistent/path.jsonl")

    def test_fallback_to_featurebench_splits_dir(self) -> None:
        splits_dir = Path(__file__).parent.parent / "benchmarks" / "featurebench-splits"
        if (splits_dir / "val.jsonl").exists():
            ids = bench.load_task_ids_from_split("val.jsonl")
            assert len(ids) > 0
            assert all(isinstance(i, str) for i in ids)


class TestExtractPatch:
    def test_extracts_diff(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "file.py").write_text("# original\n")
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        initial = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        (repo / "file.py").write_text("# modified\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "change"],
            cwd=repo, capture_output=True, check=True,
        )

        patch = bench.extract_patch(repo, initial)
        assert "# original" in patch
        assert "# modified" in patch
        assert patch.startswith("diff --git")

    def test_empty_patch_when_no_changes(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "file.py").write_text("# same\n")
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        patch = bench.extract_patch(repo, sha)
        assert patch.strip() == ""


class TestCompare:
    def test_both_resolved(self) -> None:
        factory = {"tasks": [
            {"instance_id": "t1", "resolved": True, "score": 1.0},
        ]}
        baseline = {"tasks": [
            {"instance_id": "t1", "resolved": True, "score": 0.8},
        ]}
        report = bench.compare(factory, baseline)
        assert report["total_tasks"] == 1
        assert report["factory_resolved"] == 1
        assert report["baseline_resolved"] == 1
        assert report["only_factory_solved"] == []
        assert report["only_baseline_solved"] == []

    def test_only_factory_solved(self) -> None:
        factory = {"tasks": [
            {"instance_id": "t1", "resolved": True, "score": 1.0},
            {"instance_id": "t2", "resolved": False, "score": 0.0},
        ]}
        baseline = {"tasks": [
            {"instance_id": "t1", "resolved": False, "score": 0.0},
            {"instance_id": "t2", "resolved": False, "score": 0.0},
        ]}
        report = bench.compare(factory, baseline)
        assert report["factory_resolved"] == 1
        assert report["baseline_resolved"] == 0
        assert report["only_factory_solved"] == ["t1"]

    def test_disjoint_tasks(self) -> None:
        factory = {"tasks": [{"instance_id": "f1", "resolved": True}]}
        baseline = {"tasks": [{"instance_id": "b1", "resolved": True}]}
        report = bench.compare(factory, baseline)
        assert report["total_tasks"] == 2
        assert set(report["only_factory_solved"]) == {"f1"}
        assert set(report["only_baseline_solved"]) == {"b1"}

    def test_empty_results(self) -> None:
        report = bench.compare({"tasks": []}, {"tasks": []})
        assert report["total_tasks"] == 0

    def test_resolve_rate_calculation(self) -> None:
        factory = {"tasks": [
            {"instance_id": f"t{i}", "resolved": i < 3} for i in range(10)
        ]}
        baseline = {"tasks": [
            {"instance_id": f"t{i}", "resolved": i < 5} for i in range(10)
        ]}
        report = bench.compare(factory, baseline)
        assert report["factory_resolve_rate"] == pytest.approx(0.3)
        assert report["baseline_resolve_rate"] == pytest.approx(0.5)

    def test_uses_success_field_as_fallback(self) -> None:
        factory = {"tasks": [
            {"instance_id": "t1", "success": True},
        ]}
        baseline = {"tasks": [
            {"instance_id": "t1", "success": False},
        ]}
        report = bench.compare(factory, baseline)
        assert report["factory_resolved"] == 1
        assert report["baseline_resolved"] == 0


class TestIndexTasks:
    def test_from_tasks_list(self) -> None:
        result = bench._index_tasks({"tasks": [
            {"instance_id": "a", "resolved": True},
            {"instance_id": "b", "resolved": False},
        ]})
        assert set(result.keys()) == {"a", "b"}

    def test_from_per_task_list(self) -> None:
        result = bench._index_tasks({"per_task": [
            {"instance_id": "x", "factory_resolved": True},
        ]})
        assert "x" in result

    def test_empty_dict(self) -> None:
        assert bench._index_tasks({}) == {}


class TestSetupTask:
    @patch("bench.load_task_metadata")
    @patch("bench._docker_create", return_value="container123")
    @patch("bench._docker_cp")
    @patch("bench._docker_rm")
    def test_setup_creates_git_repo(
        self,
        mock_rm: MagicMock,
        mock_cp: MagicMock,
        mock_create: MagicMock,
        mock_meta: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_meta.return_value = {"docker_image": "test-image:latest", "instance_id": "t1"}

        def fake_cp(container_id: str, src: str, dst: str) -> None:
            dst_path = Path(dst)
            if dst_path.suffix == ".md":
                dst_path.write_text("# Problem\nDo something.")
            else:
                dst_path.mkdir(parents=True, exist_ok=True)
                (dst_path / "setup.py").write_text("# setup\n")

        mock_cp.side_effect = fake_cp

        task_dir, sha = bench.setup_task("t1", work_dir=tmp_path)
        assert task_dir.exists()
        assert len(sha) == 40
        assert (task_dir / ".git").is_dir()
        mock_create.assert_called_once_with("test-image:latest")
        mock_rm.assert_called_once_with("container123")

    @patch("bench.load_task_metadata")
    def test_raises_on_missing_docker_image(
        self, mock_meta: MagicMock,
    ) -> None:
        mock_meta.return_value = {"instance_id": "t1"}
        with pytest.raises(ValueError, match="No docker_image"):
            bench.setup_task("t1")


class TestRunFactory:
    @patch("bench.extract_patch", return_value="diff --git a/f.py b/f.py\n-old\n+new")
    @patch("subprocess.run")
    def test_returns_entry_on_success(self, mock_run: MagicMock, mock_patch: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        repo = tmp_path / "testbed"
        repo.mkdir()

        entry = bench.run_factory("task_x", repo, "a" * 40, timeout=60)

        assert entry["instance_id"] == "task_x"
        assert entry["agent"] == "factory_workflow"
        assert entry["model"] == "factory-featurebench"
        assert entry["model_patch"] == "diff --git a/f.py b/f.py\n-old\n+new"
        assert entry["success"] is True

    @patch("subprocess.run")
    def test_handles_timeout(self, mock_run: MagicMock) -> None:
        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and cmd[0] == "factory":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        mock_run.side_effect = side_effect

        with patch("bench.extract_patch", return_value=""):
            entry = bench.run_factory("task_timeout", Path("/tmp/fake"), "abc123", timeout=10)

        assert entry["success"] is False
        assert entry["instance_id"] == "task_timeout"


class TestRunBaseline:
    @patch("subprocess.run")
    def test_constructs_correct_fb_command(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        result = bench.run_baseline(
            ["task_a", "task_b"],
            model="claude-sonnet-4-20250514",
            results_dir=tmp_path,
        )

        assert result == tmp_path / "baseline"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["fb", "infer"]
        assert cmd[cmd.index("--agent") + 1] == "claude_code"
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-20250514"
        assert "--output-dir" in cmd
        assert "task_a" in cmd
        assert "task_b" in cmd


class TestBuildParser:
    def test_defaults(self) -> None:
        parser = bench.build_parser()
        args = parser.parse_args(["--task-id", "abc"])
        assert args.task_ids == ["abc"]
        assert args.timeout == 1800
        assert args.model == "claude-sonnet-4-20250514"
        assert args.factory_only is False
        assert args.baseline_only is False
        assert args.skip_eval is False

    def test_multiple_task_ids(self) -> None:
        parser = bench.build_parser()
        args = parser.parse_args(["--task-id", "a", "b", "c"])
        assert args.task_ids == ["a", "b", "c"]

    def test_split_flag(self) -> None:
        parser = bench.build_parser()
        args = parser.parse_args(["--split", "val.jsonl"])
        assert args.split == "val.jsonl"

    def test_factory_only_flag(self) -> None:
        parser = bench.build_parser()
        args = parser.parse_args(["--task-id", "x", "--factory-only"])
        assert args.factory_only is True

    def test_custom_results_dir(self) -> None:
        parser = bench.build_parser()
        args = parser.parse_args(["--task-id", "x", "--results-dir", "/tmp/out"])
        assert args.results_dir == Path("/tmp/out")


class TestResolveTaskIds:
    def test_from_task_id_args(self) -> None:
        args = argparse.Namespace(task_ids=["a", "b"], split=None)
        assert bench._resolve_task_ids(args) == ["a", "b"]

    def test_from_split(self, tmp_path: Path) -> None:
        split_file = tmp_path / "s.jsonl"
        split_file.write_text('{"instance_id": "z"}\n')
        args = argparse.Namespace(task_ids=None, split=str(split_file))
        assert bench._resolve_task_ids(args) == ["z"]

    def test_empty_when_neither(self) -> None:
        args = argparse.Namespace(task_ids=None, split=None)
        assert bench._resolve_task_ids(args) == []


class TestEvaluate:
    def test_skips_when_no_output_jsonl(self, tmp_path: Path) -> None:
        result = bench.evaluate(tmp_path)
        assert result is None

    @patch("subprocess.run")
    def test_parses_eval_json_file(self, mock_run: MagicMock, tmp_path: Path) -> None:
        (tmp_path / "output.jsonl").write_text('{"instance_id": "t1"}\n')
        eval_result = {"tasks": [{"instance_id": "t1", "resolved": True}]}
        (tmp_path / "run-featurebench-full.json").write_text(json.dumps(eval_result))

        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = bench.evaluate(tmp_path)
        assert result is not None
        assert result["tasks"][0]["resolved"] is True

    @patch("subprocess.run")
    def test_returns_none_on_eval_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        (tmp_path / "output.jsonl").write_text('{"instance_id": "t1"}\n')
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "eval error")
        result = bench.evaluate(tmp_path)
        assert result is None


class TestGitInit:
    def test_creates_repo_with_initial_commit(self, tmp_path: Path) -> None:
        repo = tmp_path / "project"
        repo.mkdir()
        (repo / "main.py").write_text("print('hello')\n")

        sha = bench._git_init(repo)
        assert len(sha) == 40
        assert (repo / ".git").is_dir()

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=repo,
            capture_output=True, text=True, check=True,
        )
        assert "initial" in log.stdout
