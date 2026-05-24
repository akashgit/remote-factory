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
from factory.store import ExperimentStore, ensure_factory_dir


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

    async def test_finalize_persists_scores_and_delta(self, store, sample_config):
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="H1", change_summary="stuff",
            issue_number=None, pr_number=None,
            score_before=0.80, score_after=0.85, delta=None,
            verdict="keep", cost_usd=None, notes="",
        )
        await store.finalize(exp_id, record)
        records = await store.load_history()
        assert len(records) == 1
        assert records[0].score_before == 0.80
        assert records[0].score_after == 0.85
        assert records[0].delta == 0.05


class TestReadConfig:
    async def test_read_config(self, store, sample_config):
        await store.init(sample_config)
        config = await store.read_config()
        assert config.goal == sample_config.goal
        assert config.eval_threshold == sample_config.eval_threshold

    async def test_read_config_missing_file(self, store):
        store.factory_dir.mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError, match="Run 'factory init'"):
            await store.read_config()

    async def test_read_config_invalid_json(self, store):
        store.factory_dir.mkdir(parents=True, exist_ok=True)
        (store.factory_dir / "config.json").write_text("{bad json")
        with pytest.raises(ValueError, match="invalid JSON"):
            await store.read_config()

    async def test_read_config_invalid_schema(self, store):
        store.factory_dir.mkdir(parents=True, exist_ok=True)
        (store.factory_dir / "config.json").write_text('{"not_a_field": true}')
        with pytest.raises(ValueError, match="failed validation"):
            await store.read_config()


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


class TestStatePersistence:
    """Tests for experiment state persistence edge cases (issue #12)."""

    async def test_finalize_missing_experiment_dir(self, store, sample_config):
        """finalize() should create the experiment dir if it was deleted (e.g. git clean)."""
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        # Simulate git clean wiping the experiment dir
        exp_dir = store.factory_dir / "experiments" / f"{exp_id:03d}"
        import shutil
        shutil.rmtree(exp_dir)
        assert not exp_dir.exists()

        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="H1", change_summary="stuff",
            issue_number=None, pr_number=None,
            score_before=0.8, score_after=0.9, delta=0.1,
            verdict="keep", cost_usd=None, notes="",
        )
        # Should NOT raise FileNotFoundError
        await store.finalize(exp_id, record)
        assert (exp_dir / "verdict.json").exists()

    async def test_begin_idempotent_no_crash(self, store, sample_config):
        """begin() does not crash if the experiment dir already exists."""
        await store.init(sample_config)
        exp_id = await store.begin("H1")
        exp_dir = store.factory_dir / "experiments" / f"{exp_id:03d}"
        assert exp_dir.exists()
        # Calling begin() again should succeed (creates next experiment)
        exp_id2 = await store.begin("H2")
        assert exp_id2 == exp_id + 1

    async def test_begin_does_not_overwrite_hypothesis(self, store, sample_config):
        """begin() should not overwrite an existing hypothesis.md in the dir."""
        await store.init(sample_config)
        exp_id = await store.begin("Original")
        exp_dir = store.factory_dir / "experiments" / f"{exp_id:03d}"
        assert exp_dir.exists()
        assert (exp_dir / "hypothesis.md").read_text() == "Original"
        # Simulate a re-run: delete the hypothesis and call begin with same dir
        # Since next_id skips past existing dirs, we test the mkdir exist_ok path
        # by pre-creating the next dir before calling begin
        next_id = exp_id + 1
        next_dir = store.factory_dir / "experiments" / f"{next_id:03d}"
        next_dir.mkdir(parents=True, exist_ok=True)
        (next_dir / "hypothesis.md").write_text("Pre-existing")
        # next_id() will see dirs 001 and 002, return 3
        id3 = await store.begin("Third")
        assert id3 == next_id + 1
        # Pre-existing hypothesis should be untouched
        assert (next_dir / "hypothesis.md").read_text() == "Pre-existing"

    async def test_finalize_then_load_history_roundtrip(self, store, sample_config):
        """finalize() followed by load_history() should return the same data."""
        await store.init(sample_config)
        exp_id = await store.begin("Increase coverage")
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime(2025, 1, 15, 12, 0, 0),
            hypothesis="Increase coverage",
            change_summary="Added tests for edge cases",
            issue_number=42, pr_number=99,
            score_before=0.75, score_after=0.92, delta=0.17,
            verdict="keep", cost_usd=1.23, notes="All green",
        )
        await store.finalize(exp_id, record)
        history = await store.load_history()
        assert len(history) == 1
        loaded = history[0]
        assert loaded.id == exp_id
        assert loaded.hypothesis == "Increase coverage"
        assert loaded.change_summary == "Added tests for edge cases"
        assert loaded.issue_number == 42
        assert loaded.pr_number == 99
        assert loaded.score_before == 0.75
        assert loaded.score_after == 0.92
        assert loaded.delta == 0.17
        assert loaded.verdict == "keep"
        assert loaded.cost_usd == 1.23
        assert loaded.notes == "All green"


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


class TestReparseResearchTarget:
    """Tests for parsing research mode sections from factory.md."""

    async def test_parse_research_target(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n\n"
            "## Research Target\n"
            "- Objective: Minimize latency\n"
            "- Metric: p99_ms\n"
            "- Target: 50.0\n"
            "- Run Command: python benchmark.py\n"
            "- Result Path: results/bench.json\n"
            "- Result Parser: json\n"
            "- Timeout: 1800\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.research_target is not None
        assert config.research_target.objective == "Minimize latency"
        assert config.research_target.metric == "p99_ms"
        assert config.research_target.target == 50.0
        assert config.research_target.run_command == "python benchmark.py"
        assert config.research_target.result_path == "results/bench.json"
        assert config.research_target.result_parser == "json"
        assert config.research_target.timeout == 1800

    async def test_parse_mutable_and_fixed_surfaces(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Mutable Surfaces\n"
            "- src/model.py\n"
            "- src/optimizer.py\n\n"
            "## Fixed Surfaces\n"
            "- data/\n"
            "- configs/\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.mutable_surfaces == ["src/model.py", "src/optimizer.py"]
        assert config.fixed_surfaces == ["data/", "configs/"]

    async def test_parse_research_constraints(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Research Constraints\n"
            "- No extra dependencies\n"
            "- Must keep backward compat\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.research_constraints == ["No extra dependencies", "Must keep backward compat"]

    async def test_parse_cost_budget(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Cost Budget\n"
            "- Max per cycle: 5.0\n"
            "- Max total: 50.0\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.cost_budget is not None
        assert config.cost_budget.max_per_cycle == 5.0
        assert config.cost_budget.max_total == 50.0

    async def test_backward_compat_no_research_sections(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nNormal project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.research_target is None
        assert config.mutable_surfaces == []
        assert config.fixed_surfaces == []
        assert config.research_constraints == []
        assert config.cost_budget is None

    async def test_case_insensitive_research_keys(self, store):
        """Keys like 'objective' and 'Objective' should both work."""
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Research Target\n"
            "- objective: Minimize loss\n"
            "- metric: val_loss\n"
            "- target: 0.01\n"
            "- run command: python train.py\n"
            "- result path: metrics.json\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.research_target is not None
        assert config.research_target.objective == "Minimize loss"
        assert config.research_target.run_command == "python train.py"

    async def test_incomplete_research_target_returns_none(self, store):
        """Missing required fields should produce None, not crash."""
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nResearch\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Research Target\n"
            "- Objective: Minimize loss\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.research_target is None


class TestTierWeightsParsing:
    """Tests for parsing ## Hygiene Weights and ## Growth Weights from factory.md."""

    async def test_parse_hygiene_weights(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n\n"
            "## Hygiene Weights\n"
            "- tests: 0.40\n"
            "- coverage: 0.30\n"
            "- lint: 0.10\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.hygiene_weights is not None
        assert config.hygiene_weights.tests == 0.40
        assert config.hygiene_weights.coverage == 0.30
        assert config.hygiene_weights.lint == 0.10
        assert config.hygiene_weights.type_check is None

    async def test_parse_growth_weights(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Growth Weights\n"
            "- capability_surface: 0.30\n"
            "- spec_compliance: 0.20\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.growth_weights is not None
        assert config.growth_weights.capability_surface == 0.30
        assert config.growth_weights.spec_compliance == 0.20
        assert config.growth_weights.observability is None

    async def test_both_tier_weights(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Hygiene Weights\n"
            "- tests: 0.50\n\n"
            "## Growth Weights\n"
            "- factory_effectiveness: 0.25\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.hygiene_weights is not None
        assert config.hygiene_weights.tests == 0.50
        assert config.growth_weights is not None
        assert config.growth_weights.factory_effectiveness == 0.25

    async def test_no_tier_weights_backward_compat(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nNormal project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.hygiene_weights is None
        assert config.growth_weights is None

    async def test_invalid_dim_name_ignored(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Hygiene Weights\n"
            "- tests: 0.40\n"
            "- nonexistent_dim: 0.99\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.hygiene_weights is not None
        assert config.hygiene_weights.tests == 0.40

    async def test_tier_weights_roundtrip_config_json(self, store, sample_config):
        """TierWeights should survive write → read via config.json."""
        from factory.models import TierWeights
        config = sample_config.model_copy(update={
            "hygiene_weights": TierWeights(tests=0.40, lint=0.20),
        })
        await store.init(config)
        loaded = await store.read_config()
        assert loaded.hygiene_weights is not None
        assert loaded.hygiene_weights.tests == 0.40
        assert loaded.hygiene_weights.lint == 0.20
        assert loaded.hygiene_weights.coverage is None


class TestEnsureFactoryDir:
    """Regression tests for broken symlink handling (issue #276)."""

    def test_creates_directory(self, tmp_path):
        target = tmp_path / ".factory"
        ensure_factory_dir(target)
        assert target.is_dir()

    def test_existing_directory_is_noop(self, tmp_path):
        target = tmp_path / ".factory"
        target.mkdir()
        (target / "config.json").write_text("{}")
        ensure_factory_dir(target)
        assert target.is_dir()
        assert (target / "config.json").read_text() == "{}"

    def test_broken_symlink_replaced_with_directory(self, tmp_path):
        target = tmp_path / ".factory"
        target.symlink_to("/nonexistent/path/that/does/not/exist")
        assert target.is_symlink()
        assert not target.exists()
        ensure_factory_dir(target)
        assert target.is_dir()
        assert not target.is_symlink()

    async def test_store_init_with_broken_symlink(self, tmp_path, sample_config):
        """ExperimentStore.init handles a broken symlink at .factory/ gracefully."""
        project = tmp_path / "project"
        project.mkdir()
        broken_link = project / ".factory"
        broken_link.symlink_to("/nonexistent/absolute/path")
        assert broken_link.is_symlink()
        assert not broken_link.exists()

        store = ExperimentStore(project)
        await store.init(sample_config)
        assert store.factory_dir.is_dir()
        assert not store.factory_dir.is_symlink()
        assert (store.factory_dir / "config.json").exists()
