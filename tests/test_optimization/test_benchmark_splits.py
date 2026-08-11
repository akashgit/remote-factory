"""Tests for BenchmarkSplits — construction, validation, JSONL round-trip, get_ids."""

from __future__ import annotations

from pathlib import Path

from factory.optimization.types import BenchmarkSplits


class TestBenchmarkSplitsConstruction:
    def test_defaults_empty(self) -> None:
        splits = BenchmarkSplits()
        assert splits.train_ids == []
        assert splits.dev_ids == []
        assert splits.eval_ids == []
        assert splits.test_ids == []

    def test_explicit_ids(self) -> None:
        splits = BenchmarkSplits(
            train_ids=["a", "b"],
            dev_ids=["c"],
            eval_ids=["d"],
            test_ids=["e"],
        )
        assert splits.train_ids == ["a", "b"]
        assert splits.test_ids == ["e"]


class TestGetIds:
    def test_get_ids_returns_copy(self) -> None:
        splits = BenchmarkSplits(train_ids=["a", "b"])
        result = splits.get_ids("train")
        result.append("c")
        assert splits.train_ids == ["a", "b"]

    def test_get_ids_all_splits(self) -> None:
        splits = BenchmarkSplits(
            train_ids=["t1"],
            dev_ids=["d1"],
            eval_ids=["e1"],
            test_ids=["s1"],
        )
        assert splits.get_ids("train") == ["t1"]
        assert splits.get_ids("dev") == ["d1"]
        assert splits.get_ids("eval") == ["e1"]
        assert splits.get_ids("test") == ["s1"]


class TestValidation:
    def test_no_warnings_for_valid_splits(self) -> None:
        splits = BenchmarkSplits(
            train_ids=["a", "b"],
            dev_ids=["c"],
            eval_ids=["d"],
            test_ids=["e"],
        )
        assert splits.validate() == []

    def test_overlap_detected(self) -> None:
        splits = BenchmarkSplits(
            train_ids=["a", "b"],
            dev_ids=["b", "c"],
            test_ids=["d"],
        )
        warnings = splits.validate()
        assert any("overlap" in w for w in warnings)
        assert any("train" in w and "dev" in w for w in warnings)

    def test_empty_dev_warning(self) -> None:
        splits = BenchmarkSplits(train_ids=["a"], test_ids=["b"])
        warnings = splits.validate()
        assert any("dev split is empty" in w for w in warnings)

    def test_empty_test_warning(self) -> None:
        splits = BenchmarkSplits(train_ids=["a"], dev_ids=["b"])
        warnings = splits.validate()
        assert any("test split is empty" in w for w in warnings)

    def test_multiple_overlaps(self) -> None:
        splits = BenchmarkSplits(
            train_ids=["x"],
            dev_ids=["x"],
            eval_ids=["x"],
            test_ids=["x"],
        )
        warnings = splits.validate()
        overlap_warnings = [w for w in warnings if "overlap" in w]
        assert len(overlap_warnings) >= 3


class TestJsonlRoundTrip:
    def test_round_trip(self, tmp_path: Path) -> None:
        original = BenchmarkSplits(
            train_ids=["a", "b", "c"],
            dev_ids=["d", "e"],
            eval_ids=["f"],
            test_ids=["g", "h"],
        )
        original.to_jsonl_dir(tmp_path / "splits")
        loaded = BenchmarkSplits.from_jsonl_dir(tmp_path / "splits")
        assert loaded.train_ids == original.train_ids
        assert loaded.dev_ids == original.dev_ids
        assert loaded.eval_ids == original.eval_ids
        assert loaded.test_ids == original.test_ids

    def test_empty_splits_round_trip(self, tmp_path: Path) -> None:
        original = BenchmarkSplits(train_ids=["a"], dev_ids=["b"])
        original.to_jsonl_dir(tmp_path / "splits")
        loaded = BenchmarkSplits.from_jsonl_dir(tmp_path / "splits")
        assert loaded.eval_ids == []
        assert loaded.test_ids == []

    def test_from_jsonl_dir_missing_files(self, tmp_path: Path) -> None:
        (tmp_path / "splits").mkdir()
        loaded = BenchmarkSplits.from_jsonl_dir(tmp_path / "splits")
        assert loaded.train_ids == []
        assert loaded.dev_ids == []
