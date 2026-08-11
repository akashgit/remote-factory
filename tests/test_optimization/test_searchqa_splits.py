"""Tests for create_searchqa_splits — deterministic partitioning."""

from __future__ import annotations

from pathlib import Path

from factory.optimization.benchmarks.searchqa import create_searchqa_splits


def _make_tasks(tmp_path: Path, n: int) -> Path:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    for i in range(n):
        (tasks_dir / f"task_{i:03d}").mkdir()
    return tasks_dir


class TestCreateSearchqaSplits:
    def test_correct_sizes_100_tasks(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 100)
        splits = create_searchqa_splits(tasks_dir)
        assert len(splits.train_ids) == 60
        assert len(splits.dev_ids) == 20
        assert len(splits.eval_ids) == 10
        assert len(splits.test_ids) == 10

    def test_all_ids_accounted_for(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 50)
        splits = create_searchqa_splits(tasks_dir)
        all_ids = splits.train_ids + splits.dev_ids + splits.eval_ids + splits.test_ids
        assert len(all_ids) == 50
        assert len(set(all_ids)) == 50

    def test_deterministic_same_seed(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 30)
        s1 = create_searchqa_splits(tasks_dir, seed=42)
        s2 = create_searchqa_splits(tasks_dir, seed=42)
        assert s1.train_ids == s2.train_ids
        assert s1.dev_ids == s2.dev_ids
        assert s1.test_ids == s2.test_ids

    def test_different_seed_different_partition(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 30)
        s1 = create_searchqa_splits(tasks_dir, seed=42)
        s2 = create_searchqa_splits(tasks_dir, seed=99)
        assert s1.train_ids != s2.train_ids

    def test_no_cross_split_overlap(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 100)
        splits = create_searchqa_splits(tasks_dir)
        assert splits.validate() == []

    def test_custom_ratios(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 100)
        splits = create_searchqa_splits(
            tasks_dir, train_ratio=0.5, dev_ratio=0.3, eval_ratio=0.1, test_ratio=0.1,
        )
        assert len(splits.train_ids) == 50
        assert len(splits.dev_ids) == 30
        assert len(splits.eval_ids) == 10
        assert len(splits.test_ids) == 10

    def test_small_dataset(self, tmp_path: Path) -> None:
        tasks_dir = _make_tasks(tmp_path, 5)
        splits = create_searchqa_splits(tasks_dir)
        all_ids = splits.train_ids + splits.dev_ids + splits.eval_ids + splits.test_ids
        assert len(all_ids) == 5
