"""Tests for Phase 4 features: CLI, checkpoints, progress, LLM crossover, timeout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.outer_loop.checkpoint import CheckpointData, load_latest_checkpoint, save_checkpoint
from factory.outer_loop.models import Individual, MutationRecord, MutationType
from factory.outer_loop.mutations import _crossover_prompts, llm_crossover_prompt
from factory.outer_loop.progress import ProgressTracker


class TestCLIEntryPoints:
    """Test outer-loop CLI subcommand parsing."""

    def test_calibrate_help_parses(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args(["outer-loop", "calibrate", "--project", "/tmp/p"])
        assert ns.outer_loop_command == "calibrate"
        assert ns.project == "/tmp/p"

    def test_calibrate_parallelism(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args(
            ["outer-loop", "calibrate", "--project", "/tmp/p", "--parallelism", "8"]
        )
        assert ns.parallelism == 8

    def test_calibrate_timeout(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args(
            ["outer-loop", "calibrate", "--project", "/tmp/p", "--timeout", "3600"]
        )
        assert ns.timeout == 3600

    def test_evolve_help_parses(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args(["outer-loop", "evolve", "--project", "/tmp/p"])
        assert ns.outer_loop_command == "evolve"
        assert ns.project == "/tmp/p"

    def test_evolve_all_args(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args([
            "outer-loop", "evolve",
            "--project", "/tmp/p",
            "--generations", "5",
            "--population", "8",
            "--parallelism", "4",
            "--budget", "100",
            "--timeout", "2400",
            "--resume",
        ])
        assert ns.generations == 5
        assert ns.population == 8
        assert ns.parallelism == 4
        assert ns.budget == 100
        assert ns.timeout == 2400
        assert ns.resume is True

    def test_evolve_defaults(self) -> None:
        from factory.cli._main import build_parser

        parser = build_parser()
        ns = parser.parse_args(["outer-loop", "evolve", "--project", "/tmp/p"])
        assert ns.generations == 3
        assert ns.population == 6
        assert ns.parallelism == 4
        assert ns.budget == 40
        assert ns.timeout == 1800
        assert ns.resume is False


class TestCheckpointSaveLoad:
    """Test checkpoint round-trip serialization."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        cp = CheckpointData(
            generation=2,
            population=[],
            budget_consumed=10,
            budget_total=40,
        )
        path = save_checkpoint(tmp_path, cp)
        assert path.exists()
        assert "checkpoint_gen_2" in path.name

    def test_atomic_write(self, tmp_path: Path) -> None:
        """No .tmp file should remain after save."""
        cp = CheckpointData(generation=0, population=[])
        save_checkpoint(tmp_path, cp)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_round_trip(self, tmp_path: Path) -> None:
        ind = Individual(
            id="test123",
            workflow_data={"name": "test", "nodes": {}, "edges": [], "start_node": "s", "terminal": False},
            score=0.75,
            features=(3, 1, 4, 2),
            generation=1,
        )
        cp = CheckpointData(
            generation=3,
            population=[ind],
            best_individual=ind,
            score_trajectory=[0.5, 0.6, 0.75],
            budget_consumed=15,
            budget_total=40,
            calibration_path="/tmp/cal.json",
        )
        save_checkpoint(tmp_path, cp)
        loaded = load_latest_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded.generation == 3
        assert len(loaded.population) == 1
        assert loaded.population[0].id == "test123"
        assert loaded.population[0].score == pytest.approx(0.75)
        assert loaded.score_trajectory == [0.5, 0.6, 0.75]
        assert loaded.budget_consumed == 15

    def test_load_latest_picks_highest_gen(self, tmp_path: Path) -> None:
        for gen in [0, 1, 2]:
            save_checkpoint(tmp_path, CheckpointData(generation=gen, population=[]))
        loaded = load_latest_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded.generation == 2

    def test_load_empty_dir_returns_none(self, tmp_path: Path) -> None:
        assert load_latest_checkpoint(tmp_path) is None

    def test_mutation_history_preserved(self, tmp_path: Path) -> None:
        rec = MutationRecord(
            operator=MutationType.NODE_INSERT,
            target_node="agent_42",
            rationale="test mutation",
        )
        cp = CheckpointData(
            generation=1,
            population=[],
            mutation_history=[rec],
        )
        save_checkpoint(tmp_path, cp)
        loaded = load_latest_checkpoint(tmp_path)
        assert loaded is not None
        assert len(loaded.mutation_history) == 1
        assert loaded.mutation_history[0].operator == MutationType.NODE_INSERT


class TestProgressTracking:
    """Test progress JSONL file writing."""

    def test_generation_start_writes_line(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.generation_start(0, 40)
        lines = tracker.path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "generation_start"
        assert event["generation"] == 0
        assert event["budget_remaining"] == 40
        assert "timestamp" in event

    def test_generation_complete_writes_line(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.generation_complete(1, 0.8, 0.5, 123.45)
        lines = tracker.path.read_text().strip().splitlines()
        event = json.loads(lines[0])
        assert event["event_type"] == "generation_complete"
        assert event["best_score"] == pytest.approx(0.8)
        assert event["duration_seconds"] == pytest.approx(123.45)

    def test_eval_complete_writes_per_instance(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.eval_complete(0, "wf_abc", "pydantic-123", 0.6, "resolved", 45.2)
        lines = tracker.path.read_text().strip().splitlines()
        event = json.loads(lines[0])
        assert event["event_type"] == "eval_complete"
        assert event["instance_id"] == "pydantic-123"
        assert event["score"] == pytest.approx(0.6)

    def test_checkpoint_saved_event(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.checkpoint_saved(2, "/tmp/checkpoint_gen_2.json")
        lines = tracker.path.read_text().strip().splitlines()
        event = json.loads(lines[0])
        assert event["event_type"] == "checkpoint_saved"
        assert event["generation"] == 2

    def test_timeout_event(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.timeout_event(1, "fastapi-123", "builder", 1800, retry=True)
        lines = tracker.path.read_text().strip().splitlines()
        event = json.loads(lines[0])
        assert event["event_type"] == "timeout"
        assert event["retry"] is True
        assert event["original_timeout"] == 1800

    def test_append_only(self, tmp_path: Path) -> None:
        tracker = ProgressTracker(tmp_path)
        tracker.generation_start(0, 40)
        tracker.generation_start(1, 38)
        tracker.generation_complete(0, 0.5, 0.3, 60.0)
        lines = tracker.path.read_text().strip().splitlines()
        assert len(lines) == 3


class TestLLMCrossover:
    """Test optional LLM crossover function."""

    def test_llm_crossover_prompt_returns_string(self) -> None:
        prompt = llm_crossover_prompt("Parent A prompt.", "Parent B prompt.")
        assert isinstance(prompt, str)
        assert "Parent A" in prompt
        assert "Parent B" in prompt
        assert "Parent A prompt." in prompt
        assert "Parent B prompt." in prompt

    def test_crossover_fn_none_falls_back_to_sentence_shuffle(self) -> None:
        result = _crossover_prompts("A. B. C.", "D. E. F.", "builder", crossover_fn=None)
        assert isinstance(result, str)
        assert result.endswith(".")

    def test_crossover_fn_provided_uses_it(self) -> None:
        def custom_fn(a: str, b: str) -> str:
            return f"COMBINED: {a} + {b}"

        result = _crossover_prompts("parent A", "parent B", "builder", crossover_fn=custom_fn)
        assert result == "COMBINED: parent A + parent B"

    def test_crossover_fn_with_empty_current(self) -> None:
        """When current is empty, returns donor regardless of crossover_fn."""
        result = _crossover_prompts("", "donor prompt", "builder", crossover_fn=lambda a, b: "never")
        assert result == "donor prompt"

    def test_crossover_fn_with_empty_donor(self) -> None:
        """When donor is empty, returns current regardless of crossover_fn."""
        result = _crossover_prompts("current prompt", "", "builder", crossover_fn=lambda a, b: "never")
        assert result == "current prompt"
