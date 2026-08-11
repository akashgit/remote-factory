"""Tests for HarborBenchmark split support — symlink creation and split-aware execute."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from factory.optimization.benchmarks.harbor import HarborBenchmark
from factory.optimization.surface import Surface
from factory.optimization.types import BenchmarkSplits


class TestCreateSplitDir:
    def test_creates_symlinks(self, tmp_path: Path) -> None:
        source = tmp_path / "benchmarks" / "searchqa-harbor" / "train"
        source.mkdir(parents=True)
        for tid in ["task_001", "task_002", "task_003"]:
            (source / tid).mkdir()
            (source / tid / "data.json").write_text("{}")

        splits = BenchmarkSplits(dev_ids=["task_001", "task_003"], test_ids=["task_002"])
        hb = HarborBenchmark(splits=splits, cleanup_jobs=False)
        hb._run = 1

        split_dir = hb._create_split_dir(tmp_path, ["task_001", "task_003"], "dev")
        assert (split_dir / "task_001").is_symlink()
        assert (split_dir / "task_003").is_symlink()
        assert not (split_dir / "task_002").exists()
        assert (split_dir / "task_001" / "data.json").exists()

    def test_skips_missing_tasks(self, tmp_path: Path) -> None:
        source = tmp_path / "benchmarks" / "searchqa-harbor" / "train"
        source.mkdir(parents=True)
        (source / "task_001").mkdir()

        splits = BenchmarkSplits(dev_ids=["task_001", "task_missing"])
        hb = HarborBenchmark(splits=splits, cleanup_jobs=False)
        hb._run = 1

        split_dir = hb._create_split_dir(tmp_path, ["task_001", "task_missing"], "dev")
        assert (split_dir / "task_001").is_symlink()
        assert not (split_dir / "task_missing").exists()

    def test_falls_back_to_tasks_dir(self, tmp_path: Path) -> None:
        source = tmp_path / "benchmarks" / "searchqa-harbor" / "tasks"
        source.mkdir(parents=True)
        (source / "t1").mkdir()

        hb = HarborBenchmark(splits=BenchmarkSplits(dev_ids=["t1"]), cleanup_jobs=False)
        hb._run = 1

        split_dir = hb._create_split_dir(tmp_path, ["t1"], "dev")
        assert (split_dir / "t1").is_symlink()


class TestExecuteWithSplits:
    @patch("factory.optimization.benchmarks.harbor.subprocess.run")
    def test_split_passes_p_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        source = tmp_path / "benchmarks" / "searchqa-harbor" / "train"
        source.mkdir(parents=True)
        (source / "t1").mkdir()
        (source / "t2").mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        splits = BenchmarkSplits(dev_ids=["t1"], test_ids=["t2"])
        hb = HarborBenchmark(splits=splits, cleanup_jobs=False)
        hb.execute(tmp_path, Surface(), split="dev")

        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        p_val = cmd[p_idx + 1]
        assert "split-dev" in p_val

    @patch("factory.optimization.benchmarks.harbor.subprocess.run")
    def test_no_split_uses_legacy(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        hb = HarborBenchmark(cleanup_jobs=False)
        hb.execute(tmp_path, Surface(), split=None)

        cmd = mock_run.call_args[0][0]
        assert "--dataset" in cmd

    @patch("factory.optimization.benchmarks.harbor.subprocess.run")
    def test_subset_dir_takes_precedence_when_no_splits(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        subset = tmp_path / "subset"
        subset.mkdir()

        hb = HarborBenchmark(subset_dir=subset, cleanup_jobs=False)
        hb.execute(tmp_path, Surface())

        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == str(subset)
