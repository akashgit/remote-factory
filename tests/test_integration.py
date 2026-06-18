"""Integration tests — end-to-end workflows."""

import json

import pytest

from factory.cli import main
from factory.discovery.introspect import introspect_project
from factory.discovery.profile import build_eval_profile
from factory.discovery.generate import write_eval_script
from factory.store import ExperimentStore
from factory.models import FactoryConfig


class TestDiscoverToInit:
    def test_full_discovery_pipeline(self, python_project, capsys):
        """Discover -> build profile -> generate eval script."""
        project = introspect_project(python_project)
        assert project.language == "python"

        profile = build_eval_profile(project)
        assert len(profile.dimensions) > 0

        path = write_eval_script(profile, python_project)
        assert path.exists()

        # Verify the generated script is valid Python
        script = path.read_text()
        compile(script, str(path), "exec")

    def test_discover_cli_produces_valid_json(self, python_project, capsys):
        result = main(["discover", str(python_project)])
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert "project" in output
        assert "eval_profile" in output


class TestInitWorkflow:
    async def test_init_creates_factory_dir(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()

        # Write factory.md
        (project / "factory.md").write_text(
            "# Goal\nBuild something great.\n\n"
            "# Scope\n- src/**/*.py\n\n"
            "# Guards\n- Do not delete tests\n\n"
            "# Eval_command\npython eval/score.py\n\n"
            "# Eval_threshold\n0.8\n\n"
            "# Constraints\n- Keep it simple\n"
        )

        store = ExperimentStore(project)
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        await store.init(config)

        assert (project / ".factory" / "config.json").exists()
        assert (project / ".factory" / "results.tsv").exists()
        assert config.goal == "Build something great."
        assert config.eval_threshold == 0.8


class TestExperimentLifecycle:
    async def test_begin_finalize_history(self, tmp_path):
        from datetime import datetime
        from factory.models import ExperimentRecord

        project = tmp_path / "proj"
        project.mkdir()
        store = ExperimentStore(project)

        config = FactoryConfig(
            goal="test", scope=[], guards=[],
            eval_command="echo ok", eval_threshold=0.5,
            constraints=[],
        )
        await store.init(config)

        # Begin
        exp_id = await store.begin("Test hypothesis")
        assert exp_id == 1

        # Finalize
        record = ExperimentRecord(
            id=exp_id, timestamp=datetime.now(),
            hypothesis="Test hypothesis", change_summary="test",
            issue_number=None, pr_number=None,
            score_before=0.5, score_after=0.7, delta=0.2,
            verdict="keep", cost_usd=0.5, notes="",
        )
        await store.finalize(exp_id, record)

        # History
        records = await store.load_history()
        assert len(records) == 1
        assert records[0].hypothesis == "Test hypothesis"
        assert records[0].delta == pytest.approx(0.2)
