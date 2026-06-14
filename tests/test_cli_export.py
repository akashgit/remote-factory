"""Tests for the factory export CLI subcommand."""

import json
from pathlib import Path

from factory.cli import main
from factory.models import FactoryConfig
from factory.store import ExperimentStore


async def _setup_factory_project(
    project: Path,
    config: FactoryConfig,
    *,
    with_strategy: bool = False,
    with_eval_profile: bool = False,
) -> ExperimentStore:
    """Set up a .factory/ directory with config and optional artifacts."""
    store = ExperimentStore(project)
    await store.init(config)

    if with_strategy:
        await store.write_strategy("## Current Strategy\n\nFocus on tests.\n")

    if with_eval_profile:
        from factory.models import EvalDimension, EvalProfile

        profile = EvalProfile(
            project_type="python-cli",
            dimensions=[
                EvalDimension(
                    name="tests_pass",
                    command="pytest",
                    weight=1.0,
                    parser="exit_code",
                    regex_pattern=None,
                    description="All tests pass",
                    source="discovered",
                ),
            ],
            tier="discovered",
            confidence=0.8,
            human_reviewed=False,
        )
        await store.save_eval_profile(profile)

    return store


def test_export_produces_valid_json(tmp_project: Path, sample_config: FactoryConfig, capsys):
    """Export a project with .factory/ and verify valid JSON output."""
    import asyncio

    asyncio.run(_setup_factory_project(
        tmp_project, sample_config, with_strategy=True, with_eval_profile=True,
    ))

    code = main(["export", str(tmp_project)])
    assert code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    # Verify top-level keys
    assert "config" in data
    assert "eval_profile" in data
    assert "experiments" in data
    assert "strategy" in data
    assert "meta" in data

    # Verify config content
    assert data["config"]["goal"] == "Build a test project"
    assert data["config"]["eval_command"] == ""

    # Verify eval_profile content
    assert data["eval_profile"]["project_type"] == "python-cli"
    assert len(data["eval_profile"]["dimensions"]) == 1

    # Verify strategy content
    assert "Focus on tests" in data["strategy"]

    # Verify experiments is a list (empty since we didn't add any)
    assert isinstance(data["experiments"], list)
    assert len(data["experiments"]) == 0

    # Verify meta
    assert data["meta"]["factory_version"] == "0.1.0"
    assert str(tmp_project) in data["meta"]["project_path"]
    assert "timestamp" in data["meta"]


def test_export_without_factory_dir(tmp_project: Path, capsys):
    """Export fails gracefully when .factory/ doesn't exist."""
    code = main(["export", str(tmp_project)])
    assert code == 1

    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_export_minimal_factory(tmp_project: Path, sample_config: FactoryConfig, capsys):
    """Export works with only config (no eval_profile, no strategy)."""
    import asyncio

    asyncio.run(_setup_factory_project(tmp_project, sample_config))

    code = main(["export", str(tmp_project)])
    assert code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["config"]["goal"] == "Build a test project"
    assert data["eval_profile"] is None
    assert data["strategy"] is None
    assert data["experiments"] == []


def test_export_with_experiment_history(
    tmp_project: Path, sample_config: FactoryConfig, capsys
):
    """Export includes experiment records from results.tsv."""
    import asyncio
    from datetime import datetime

    from factory.models import ExperimentRecord

    async def setup():
        store = await _setup_factory_project(tmp_project, sample_config)
        exp_id = await store.begin("Add logging")
        record = ExperimentRecord(
            id=exp_id,
            timestamp=datetime.now(),
            hypothesis="Add logging",
            change_summary="Added structlog",
            issue_number=None,
            pr_number=None,
            score_before=0.5,
            score_after=0.7,
            delta=0.2,
            verdict="keep",
            cost_usd=1.50,
            notes="",
        )
        await store.finalize(exp_id, record)

    asyncio.run(setup())

    code = main(["export", str(tmp_project)])
    assert code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert len(data["experiments"]) == 1
    exp = data["experiments"][0]
    assert exp["hypothesis"] == "Add logging"
    assert exp["verdict"] == "keep"
    assert exp["delta"] == 0.2
