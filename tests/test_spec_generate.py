"""Tests for factory.spec — source file collection, batching, and W₉ workflow."""

from __future__ import annotations

from pathlib import Path

from factory.spec.generate import (
    APPROX_CHARS_PER_TOKEN,
    HAIKU_BATCH_TOKEN_LIMIT,
    collect_source_files,
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
        char_limit = HAIKU_BATCH_TOKEN_LIMIT * APPROX_CHARS_PER_TOKEN
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

    def test_extract_is_haiku(self) -> None:
        wf = spec_generate_workflow()
        extract = wf.nodes["extract"]
        assert isinstance(extract, AgentNode)
        assert extract.role == AgentRole.RESEARCHER
        assert extract.model == "haiku"

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
        assert ".factory/GRAPH-SPEC.md" in annotate.writes


# ── Registry includes W₉ ────────────────────────────────────────


class TestRegistryIncludesSpec:
    def test_register_all_includes_spec_generate(self) -> None:
        all_wf = register_all()
        assert "spec-generate" in all_wf

    def test_register_all_count(self) -> None:
        all_wf = register_all()
        assert len(all_wf) == 10

    def test_all_workflows_validate(self) -> None:
        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"{name} has validation issues: {issues}"
