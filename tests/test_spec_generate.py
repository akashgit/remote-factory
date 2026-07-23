"""Tests for factory.spec — source file collection, batching, graph summary, and generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.spec.generate import (
    APPROX_CHARS_PER_TOKEN,
    BATCH_TOKEN_LIMIT,
    _get_gitignored,
    _is_excluded_dir,
    build_graph_summary,
    collect_source_files,
    generate_spec,
    group_into_batches,
)
from factory.workflow.definitions import register_all, spec_generate_workflow
from factory.workflow.primitives import AgentNode, AgentRole, FnNode, GateNode


# ── Source file collection ───────────────────────────────────────


class TestCollectSourceFiles:
    def test_collects_python_files(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "lib.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert sorted(str(f) for f in files) == ["lib.py", "main.py"]

    def test_collects_multiple_languages(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("export const x = 1")
        (tmp_path / "main.go").write_text("package main")
        (tmp_path / "lib.rs").write_text("fn main() {}")
        files = collect_source_files(tmp_path)
        assert len(files) == 3

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")
        (tmp_path / "app.js").write_text("const x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1
        assert files[0] == Path("app.js")

    def test_excludes_factory_dir(self, tmp_path: Path) -> None:
        fd = tmp_path / ".factory"
        fd.mkdir()
        (fd / "config.py").write_text("x = 1")
        (tmp_path / "main.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1
        assert files[0] == Path("main.py")

    def test_excludes_pycache(self, tmp_path: Path) -> None:
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "module.cpython-311.pyc").write_text("")
        (tmp_path / "module.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1

    def test_excludes_venv(self, tmp_path: Path) -> None:
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1

    def test_ignores_non_source_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("# Hello")
        (tmp_path / "config.yaml").write_text("key: value")
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "main.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1
        assert files[0] == Path("main.py")

    def test_returns_relative_paths(self, tmp_path: Path) -> None:
        sub = tmp_path / "src" / "core"
        sub.mkdir(parents=True)
        (sub / "engine.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert len(files) == 1
        assert files[0] == Path("src/core/engine.py")

    def test_empty_project(self, tmp_path: Path) -> None:
        files = collect_source_files(tmp_path)
        assert files == []

    def test_sorted_output(self, tmp_path: Path) -> None:
        (tmp_path / "z.py").write_text("x = 1")
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "m.py").write_text("x = 1")
        files = collect_source_files(tmp_path)
        assert files == [Path("a.py"), Path("m.py"), Path("z.py")]


# ── File batching ────────────────────────────────────────────────


class TestGroupIntoBatches:
    def test_single_batch_small_files(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x = 1")
        files = [Path(f"f{i}.py") for i in range(5)]
        batches = group_into_batches(files, tmp_path)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_multiple_batches_large_files(self, tmp_path: Path) -> None:
        char_limit = BATCH_TOKEN_LIMIT * APPROX_CHARS_PER_TOKEN
        content = "x" * (char_limit // 2 + 1)
        for i in range(3):
            (tmp_path / f"big{i}.py").write_text(content)
        files = [Path(f"big{i}.py") for i in range(3)]
        batches = group_into_batches(files, tmp_path)
        assert len(batches) >= 2

    def test_empty_file_list(self, tmp_path: Path) -> None:
        batches = group_into_batches([], tmp_path)
        assert batches == []

    def test_custom_token_limit(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.py").write_text("x" * 100)
        files = [Path(f"f{i}.py") for i in range(10)]
        batches = group_into_batches(files, tmp_path, token_limit=50)
        assert len(batches) >= 2

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "exists.py").write_text("x = 1")
        files = [Path("exists.py"), Path("missing.py")]
        batches = group_into_batches(files, tmp_path)
        assert len(batches) == 1
        assert batches[0] == [Path("exists.py")]

    def test_oversized_file_gets_own_batch(self, tmp_path: Path) -> None:
        token_limit = 50
        char_limit = token_limit * APPROX_CHARS_PER_TOKEN  # 200

        (tmp_path / "small1.py").write_text("x" * 50)
        (tmp_path / "huge.py").write_text("x" * (char_limit + 1))
        (tmp_path / "small2.py").write_text("x" * 50)

        files = [Path("small1.py"), Path("huge.py"), Path("small2.py")]
        batches = group_into_batches(files, tmp_path, token_limit=token_limit)

        assert len(batches) == 3
        assert batches[0] == [Path("small1.py")]
        assert batches[1] == [Path("huge.py")]
        assert batches[2] == [Path("small2.py")]


# ── Graph summary ───────────────────────────────────────────────


class TestBuildGraphSummary:
    def test_empty_graph(self) -> None:
        summary = build_graph_summary({"nodes": [], "edges": []})
        assert "0 nodes" in summary
        assert "0 edges" in summary

    def test_includes_entity_counts(self) -> None:
        graph = {
            "nodes": [
                {"id": "mod_foo", "label": "Foo", "source_file": "mod.py"},
                {"id": "mod_bar", "label": "bar()", "source_file": "mod.py"},
                {"id": "mod_baz", "label": "baz()", "source_file": "mod.py"},
            ],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "function: 2" in summary
        assert "class: 1" in summary

    def test_includes_relationship_counts(self) -> None:
        graph = {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "edges": [
                {"source": "A", "target": "B", "type": "imports"},
                {"source": "B", "target": "A", "type": "imports"},
            ],
        }
        summary = build_graph_summary(graph)
        assert "imports: 2" in summary

    def test_groups_by_community(self) -> None:
        graph = {
            "nodes": [
                {"name": "Foo", "type": "class", "community": "core"},
                {"name": "Bar", "type": "class", "community": "utils"},
            ],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Community core" in summary
        assert "Community utils" in summary

    def test_ungrouped_nodes(self) -> None:
        graph = {
            "nodes": [{"name": "Foo", "type": "class"}],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Ungrouped" in summary

    def test_includes_relationships(self) -> None:
        graph = {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "edges": [{"source": "A", "target": "B", "type": "calls"}],
        }
        summary = build_graph_summary(graph)
        assert "A --[calls]--> B" in summary

    def test_uses_links_key_fallback(self) -> None:
        graph = {
            "nodes": [{"name": "X"}],
            "links": [{"source": "X", "target": "Y", "type": "imports"}],
        }
        summary = build_graph_summary(graph)
        assert "1 edges" in summary
        assert "X --[imports]--> Y" in summary

    def test_uses_group_key_fallback(self) -> None:
        graph = {
            "nodes": [{"name": "Foo", "type": "class", "group": "infra"}],
            "edges": [],
        }
        summary = build_graph_summary(graph)
        assert "Community infra" in summary

    def test_truncation_at_char_limit(self) -> None:
        nodes = [
            {"name": f"Entity{i}", "type": "class", "community": f"comm{i}"} for i in range(500)
        ]
        graph = {"nodes": nodes, "edges": []}
        summary = build_graph_summary(graph, char_limit=2000)
        assert len(summary) < 3000
        assert "truncated" in summary


# ── W₉ Spec Generate workflow ───────────────────────────────────


class TestSpecGenerateWorkflow:
    def test_validates(self) -> None:
        wf = spec_generate_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"spec-generate workflow has issues: {issues}"

    def test_name(self) -> None:
        wf = spec_generate_workflow()
        assert wf.name == "spec-generate"

    def test_start_node(self) -> None:
        wf = spec_generate_workflow()
        assert wf.start_node == "extract"

    def test_no_trigger(self) -> None:
        wf = spec_generate_workflow()
        assert wf.trigger is None

    def test_has_required_nodes(self) -> None:
        wf = spec_generate_workflow()
        expected = {
            "extract",
            "gate_extract",
            "annotate",
            "gate_annotate",
            "validate",
            "gate_validate",
        }
        assert expected == set(wf.nodes.keys())

    def test_extract_is_opus(self) -> None:
        wf = spec_generate_workflow()
        extract = wf.nodes["extract"]
        assert isinstance(extract, AgentNode)
        assert extract.role == AgentRole.RESEARCHER
        assert extract.model == "opus"

    def test_annotate_is_researcher(self) -> None:
        wf = spec_generate_workflow()
        annotate = wf.nodes["annotate"]
        assert isinstance(annotate, AgentNode)
        assert annotate.role == AgentRole.RESEARCHER

    def test_gates_are_ceo(self) -> None:
        wf = spec_generate_workflow()
        for gate_id in ("gate_extract", "gate_annotate", "gate_validate"):
            gate = wf.nodes[gate_id]
            assert isinstance(gate, GateNode)
            assert gate.evaluator_type == "agent"
            assert gate.evaluator_role == AgentRole.CEO

    def test_validate_is_fn(self) -> None:
        wf = spec_generate_workflow()
        node = wf.nodes["validate"]
        assert isinstance(node, FnNode)
        assert "factory spec validate" in node.command

    def test_extract_writes_spec_raw(self) -> None:
        wf = spec_generate_workflow()
        extract = wf.nodes["extract"]
        assert ".factory/spec_raw.md" in extract.writes

    def test_annotate_writes_repo_spec(self) -> None:
        wf = spec_generate_workflow()
        annotate = wf.nodes["annotate"]
        assert "SPEC.md" in annotate.writes


# ── Registry includes W₉ ────────────────────────────────────────


class TestRegistryIncludesSpec:
    def test_register_all_includes_spec_generate(self) -> None:
        all_wf = register_all()
        assert "spec-generate" in all_wf

    def test_register_all_count(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 23

    def test_all_workflows_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"


# ── _get_gitignored ─────────────────────────────────────────────


class TestGetGitignored:
    def test_empty_paths_returns_empty(self) -> None:
        result = _get_gitignored([], Path("/tmp"))
        assert result == set()

    @patch("factory.spec.generate.subprocess.run")
    def test_returns_ignored_paths(self, mock_run: MagicMock, tmp_path: Path) -> None:
        p1 = tmp_path / "a.py"
        p2 = tmp_path / "b.py"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"{p1}\n",
        )
        result = _get_gitignored([p1, p2], tmp_path)
        assert result == {p1}

    @patch("factory.spec.generate.subprocess.run")
    def test_returncode_1_means_none_ignored(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _get_gitignored([tmp_path / "a.py"], tmp_path)
        assert result == set()

    @patch("factory.spec.generate.subprocess.run")
    def test_error_returncode_returns_empty(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = _get_gitignored([tmp_path / "a.py"], tmp_path)
        assert result == set()


# ── _is_excluded_dir ─────────────────────────────────────────────


class TestIsExcludedDir:
    def test_exact_match(self) -> None:
        assert _is_excluded_dir("node_modules") is True

    def test_wildcard_match(self) -> None:
        assert _is_excluded_dir("mypackage.egg-info") is True

    def test_no_match(self) -> None:
        assert _is_excluded_dir("src") is False

    def test_partial_name_no_match(self) -> None:
        assert _is_excluded_dir("node_modules_extra") is False


# ── collect_source_files with git ────────────────────────────────


class TestCollectSourceFilesWithGit:
    def test_filters_gitignored_files(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "keep.py").write_text("x = 1")
        (tmp_path / "ignored.py").write_text("x = 1")

        with patch("factory.spec.generate._get_gitignored") as mock_gi:
            mock_gi.return_value = {tmp_path / "ignored.py"}
            files = collect_source_files(tmp_path)

        assert files == [Path("keep.py")]


# ── generate_spec (graph path) ──────────────────────────────────


class TestGenerateSpecGraph:
    async def test_graph_path_success(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {
            "nodes": [{"name": "main", "type": "module"}],
            "edges": [],
        }

        async def mock_invoke(role, task, project, **kwargs):
            repo_spec.write_text("# Repo spec from graph")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            result = await generate_spec(tmp_path)

        assert result == repo_spec
        assert "graph" in repo_spec.read_text().lower() or repo_spec.exists()

    async def test_graph_path_passes_summary_to_agent(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {
            "nodes": [
                {"name": "Engine", "type": "class", "community": "core"},
                {"name": "run", "type": "function", "community": "core"},
            ],
            "edges": [{"source": "Engine", "target": "run", "type": "calls"}],
        }
        captured_tasks: list[str] = []

        async def mock_invoke(role, task, project, **kwargs):
            captured_tasks.append(task)
            repo_spec.write_text("# SPEC")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            await generate_spec(tmp_path)

        assert len(captured_tasks) == 1
        assert "Code Knowledge Graph Summary" in captured_tasks[0]
        assert "Engine" in captured_tasks[0]

    async def test_graph_path_no_batch_agents(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        repo_spec = tmp_path / "SPEC.md"
        graph_data = {"nodes": [{"name": "main", "type": "module"}], "edges": []}
        invoke_calls: list[dict] = []

        async def mock_invoke(role, task, project, **kwargs):
            invoke_calls.append({"role": role, "model": kwargs.get("model")})
            repo_spec.write_text("# SPEC")
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            await generate_spec(tmp_path)

        assert len(invoke_calls) == 1
        assert invoke_calls[0]["model"] is None

    async def test_graph_annotation_failure_raises(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        graph_data = {"nodes": [], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("error", 1)),
            ),
        ):
            with pytest.raises(RuntimeError, match="Spec annotation failed"):
                await generate_spec(tmp_path)

    async def test_graph_missing_spec_raises(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1")
        graph_data = {"nodes": [], "edges": []}

        async def mock_invoke(role, task, project, **kwargs):
            return ("ok", 0)

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch("factory.agents.runner.invoke_agent", side_effect=mock_invoke),
        ):
            with pytest.raises(FileNotFoundError, match="SPEC"):
                await generate_spec(tmp_path)


# ── generate_spec (graphify pipeline errors) ─────────────────────


class TestGenerateSpecErrors:
    async def test_graphify_not_installed_raises(self, tmp_path: Path) -> None:
        with patch("factory.graph.is_graphify_installed", return_value=False):
            with pytest.raises(RuntimeError, match="graphify is required"):
                await generate_spec(tmp_path)

    async def test_extract_graph_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("factory.graph.is_graphify_installed", return_value=True),
            patch("factory.graph.extract_graph", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="graphify extraction failed"):
                await generate_spec(tmp_path)

    async def test_load_graph_failure_raises(self, tmp_path: Path) -> None:
        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="graph.json is unreadable"):
                await generate_spec(tmp_path)

    async def test_annotation_failure_raises(self, tmp_path: Path) -> None:
        graph_data = {"nodes": [{"id": "a", "label": "a.py"}], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("error", 1)),
            ),
        ):
            with pytest.raises(RuntimeError, match="Spec annotation failed"):
                await generate_spec(tmp_path)

    async def test_missing_spec_after_annotation_raises(self, tmp_path: Path) -> None:
        graph_data = {"nodes": [{"id": "a", "label": "a.py"}], "edges": []}

        with (
            patch("factory.graph.extract_graph", return_value=tmp_path / "graph.json"),
            patch("factory.graph.load_graph_data", return_value=graph_data),
            patch(
                "factory.agents.runner.invoke_agent",
                new_callable=lambda: AsyncMock(return_value=("ok", 0)),
            ),
        ):
            with pytest.raises(FileNotFoundError, match="SPEC"):
                await generate_spec(tmp_path)
