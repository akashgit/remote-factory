"""Tests for eval_spec: model field, store parsing, and discovery generation."""

import json

import pytest

from factory.discovery.eval_spec import generate_eval_spec
from factory.models import FactoryConfig, ProjectProfile
from factory.store import ExperimentStore


class TestFactoryConfigEvalSpec:
    def test_default_empty(self):
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[],
        )
        assert config.eval_spec == []

    def test_with_items(self):
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[],
            eval_spec=["Run --help", "Check health endpoint"],
        )
        assert config.eval_spec == ["Run --help", "Check health endpoint"]

    def test_roundtrip_json(self):
        config = FactoryConfig(
            goal="Test", scope=[], guards=[], eval_command="pytest",
            eval_threshold=0.8, constraints=[],
            eval_spec=["Item one", "Item two"],
        )
        data = config.model_dump()
        restored = FactoryConfig(**data)
        assert restored.eval_spec == ["Item one", "Item two"]
        assert restored == config

    def test_backward_compat_no_eval_spec(self):
        data = {
            "goal": "Test", "scope": [], "guards": [],
            "eval_command": "pytest", "eval_threshold": 0.8,
            "constraints": [],
        }
        config = FactoryConfig(**data)
        assert config.eval_spec == []


class TestStoreParseEvalSpec:
    @pytest.fixture
    def store(self, tmp_path) -> ExperimentStore:
        project = tmp_path / "project"
        project.mkdir()
        return ExperimentStore(project)

    async def test_parse_eval_spec(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest project\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n- no deletes\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n- small changes\n\n"
            "## Eval Spec\n"
            "- Run the CLI with --help and verify usage output\n"
            "- Start the server and check /health returns 200\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.eval_spec == [
            "Run the CLI with --help and verify usage output",
            "Start the server and check /health returns 200",
        ]

    async def test_parse_no_eval_spec_section(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        config = await store.reparse_config()
        assert config.eval_spec == []

    async def test_eval_spec_persisted_to_config_json(self, store):
        factory_md = store.project_path / "factory.md"
        factory_md.write_text(
            "# Factory\n\n## Goal\nTest\n\n"
            "## Scope\n- src/\n\n"
            "## Guards\n\n"
            "## Eval\n```\npython eval.py\n```\n\n"
            "## Threshold\n0.8\n\n"
            "## Constraints\n\n"
            "## Eval Spec\n"
            "- Check API schema\n"
        )
        store.factory_dir.mkdir(exist_ok=True)
        await store.reparse_config()
        data = json.loads((store.factory_dir / "config.json").read_text())
        assert data["eval_spec"] == ["Check API schema"]


class TestGenerateEvalSpec:
    def _profile(self, project_type: str, framework: str | None = None) -> ProjectProfile:
        return ProjectProfile(
            name="test",
            language="python",
            framework=framework,
            project_type=project_type,
            has_tests=True,
            has_linter=True,
            has_type_checker=False,
            has_ci=False,
        )

    def test_web_app(self, tmp_path):
        items = generate_eval_spec(self._profile("web_app"), tmp_path)
        assert len(items) >= 2
        assert any("dev server" in i.lower() or "landing page" in i.lower() for i in items)

    def test_cli_tool(self, tmp_path):
        items = generate_eval_spec(self._profile("cli_tool"), tmp_path)
        assert len(items) >= 2
        assert any("--help" in i for i in items)

    def test_service(self, tmp_path):
        items = generate_eval_spec(self._profile("service"), tmp_path)
        assert len(items) >= 2
        assert any("health" in i.lower() for i in items)

    def test_library(self, tmp_path):
        items = generate_eval_spec(self._profile("library"), tmp_path)
        assert len(items) >= 1
        assert any("import" in i.lower() for i in items)

    def test_bot(self, tmp_path):
        items = generate_eval_spec(self._profile("bot"), tmp_path)
        assert len(items) >= 1

    def test_unknown_type_gets_fallback(self, tmp_path):
        items = generate_eval_spec(self._profile("unknown"), tmp_path)
        assert len(items) >= 1

    def test_framework_adds_items(self, tmp_path):
        items_no_fw = generate_eval_spec(self._profile("web_app"), tmp_path)
        items_fastapi = generate_eval_spec(self._profile("web_app", "fastapi"), tmp_path)
        assert len(items_fastapi) > len(items_no_fw)

    def test_nextjs_framework(self, tmp_path):
        items = generate_eval_spec(self._profile("web_app", "next.js"), tmp_path)
        assert any("next" in i.lower() for i in items)

    def test_docker_detection(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.12")
        items = generate_eval_spec(self._profile("cli_tool"), tmp_path)
        assert any("docker" in i.lower() for i in items)
