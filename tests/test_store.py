"""Tests for factory.store — filesystem experiment store."""

import json
from datetime import datetime

import pytest

from factory.models import (
    CompositeScore,
    EvalDimension,
    EvalProfile,
    ExperimentRecord,
)
from factory.store import ExperimentStore


@pytest.fixture
def store(tmp_path) -> ExperimentStore:
    project = tmp_path / "project"
    project.mkdir()
    return ExperimentStore(project)


class TestInit:
    async def test_creates_structure(self, store, sample_config):
        await store.init(sample_config)
        assert (store.factory_dir / "config.json").exists()
        assert (store.factory_dir / "results.tsv").exists()
        assert (store.factory_dir / "experiments").is_dir()
        assert (store.factory_dir / "strategy").is_dir()
        assert (store.factory_dir / "agents").is_dir()

    async def test_config_json_content(self, store, sample_config):
        await store.init(sample_config)
        data = json.loads((store.factory_dir / "config.json").read_text())
        assert data["goal"] == "Build a test project"

    async def test_idempotent(self, store, sample_config):
        await store.init(sample_config)
        await store.init(sample_config)  # should not error


class TestExperiments:
    async def test_begin_returns_id(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("Test hypothesis")
        assert exp_id == 1

    async def test_sequential_ids(self, store, sample_config):
        await store.init(sample_config)
        id1 = await store.begin("H1")
        id2 = await store.begin("H2")
        assert id1 == 1
        assert id2 == 2

    async def test_begin_creates_hypothesis_file(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("My hypothesis")
        path = store.factory_dir / "experiments" / f"{exp_id:03d}" / "hypothesis.md"
        assert path.exists()
        assert path.read_text() == "My hypothesis"

    async def test_save_eval(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        score = CompositeScore(
            total=0.85, results=[], guard_violations=[], passed=True,
        )
        await store.save_eval(exp_id, "before", score)
        path = store.factory_dir / "experiments" / f"{exp_id:03d}" / "eval_before.json"
        assert path.exists()

    async def test_finalize_writes_verdict(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="H1", change_summary="Added stuff",
            issue_number=None, pr_number=None,
            score_before=0.8, score_after=0.9, delta=0.1,
            verdict="keep", cost_usd=None, notes="",
        )
        await store.finalize(exp_id, record)
        path = store.factory_dir / "experiments" / f"{exp_id:03d}" / "verdict.json"
        assert path.exists()

    async def test_finalize_appends_tsv(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="H1", change_summary="stuff",
            issue_number=None, pr_number=None,
            score_before=0.8, score_after=0.9, delta=0.1,
            verdict="keep", cost_usd=None, notes="",
        )
        await store.finalize(exp_id, record)
        records = await store.load_history()
        assert len(records) == 1
        assert records[0].verdict == "keep"


class TestReadConfig:
    async def test_read_config(self, store, sample_config):
        await store.init(sample_config)
        config = await store.read_config()
        assert config.goal == sample_config.goal
        assert config.eval_threshold == sample_config.eval_threshold


class TestEvalProfile:
    async def test_save_and_read_profile(self, store, sample_config):
        await store.init(sample_config)
        profile = EvalProfile(
            project_type="bot",
            dimensions=[
                EvalDimension(
                    name="tests", command="pytest", weight=1.0,
                    parser="exit_code", description="tests", source="discovered",
                ),
            ],
            tier="discovered",
            confidence=0.8,
        )
        await store.save_eval_profile(profile)
        loaded = await store.read_eval_profile()
        assert loaded is not None
        assert loaded.project_type == "bot"
        assert len(loaded.dimensions) == 1

    async def test_read_missing_profile(self, store, sample_config):
        await store.init(sample_config)
        assert await store.read_eval_profile() is None


class TestStrategy:
    async def test_write_and_read_strategy(self, store, sample_config):
        await store.init(sample_config)
        await store.write_strategy("## Strategy\nFocus on tests.")
        content = await store.read_strategy()
        assert content is not None
        assert "Focus on tests" in content

    async def test_read_missing_strategy(self, store, sample_config):
        await store.init(sample_config)
        assert await store.read_strategy() is None
