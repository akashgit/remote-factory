"""Tests for project eval dimensions, eval weights, and target branch features."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.models import (
    DiscoveredEval,
    EvalResult,
    EvalWeights,
    FactoryConfig,
    ProjectEvalDimension,
)


# ── Model tests ─────────────────────────────────────────────────


class TestProjectEvalDimension:
    def test_defaults(self) -> None:
        dim = ProjectEvalDimension(name="accuracy", command="python eval/bench.py")
        assert dim.parse == "json"
        assert dim.weight == 1.0
        assert dim.timeout == 300.0
        assert dim.description == ""

    def test_all_fields(self) -> None:
        dim = ProjectEvalDimension(
            name="latency",
            command="python eval/latency.py",
            parse="exit_code",
            weight=0.3,
            timeout=60.0,
            description="Measure p99 latency",
        )
        assert dim.name == "latency"
        assert dim.parse == "exit_code"
        assert dim.weight == 0.3

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            ProjectEvalDimension(
                name="x", command="y", unknown_field="z"
            )


class TestEvalWeights:
    def test_defaults(self) -> None:
        w = EvalWeights()
        assert w.hygiene == 0.50
        assert w.growth == 0.50
        assert w.project == 0.0

    def test_custom(self) -> None:
        w = EvalWeights(hygiene=0.25, growth=0.25, project=0.50)
        assert w.project == 0.50


class TestFactoryConfigNewFields:
    def test_defaults(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo",
            eval_threshold=0.8, constraints=[],
        )
        assert config.target_branch == "main"
        assert config.project_eval == []
        assert config.eval_weights.hygiene == 0.50
        assert config.eval_weights.project == 0.0

    def test_custom_target_branch(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo",
            eval_threshold=0.8, constraints=[],
            target_branch="factory/dev",
        )
        assert config.target_branch == "factory/dev"

    def test_with_project_eval(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo",
            eval_threshold=0.8, constraints=[],
            project_eval=[
                ProjectEvalDimension(name="acc", command="python bench.py"),
            ],
            eval_weights=EvalWeights(hygiene=0.3, growth=0.2, project=0.5),
        )
        assert len(config.project_eval) == 1
        assert config.eval_weights.project == 0.5

    def test_roundtrip_json(self) -> None:
        config = FactoryConfig(
            goal="test", scope=[], guards=[], eval_command="echo",
            eval_threshold=0.8, constraints=[],
            target_branch="dev",
            project_eval=[
                ProjectEvalDimension(name="x", command="y", weight=0.5),
            ],
            eval_weights=EvalWeights(hygiene=0.3, growth=0.2, project=0.5),
        )
        data = json.loads(json.dumps(config.model_dump()))
        restored = FactoryConfig(**data)
        assert restored.target_branch == "dev"
        assert len(restored.project_eval) == 1
        assert restored.eval_weights.project == 0.5


class TestDiscoveredEval:
    def test_basic(self) -> None:
        e = DiscoveredEval(name="bench", command="python bench.py")
        assert e.source == "discovered"


# ── Config parsing tests ─────────────────────────────────────────


class TestConfigParseTargetBranch:
    def test_parses_target_branch(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        (tmp_path / "factory.md").write_text(
            "## Goal\ntest\n\n## Target Branch\nfactory/dev\n\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.target_branch == "factory/dev"

    def test_defaults_to_main(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        (tmp_path / "factory.md").write_text(
            "## Goal\ntest\n\n## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.target_branch == "main"


class TestConfigParseProjectEval:
    def test_parses_single_dimension(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Project Eval\n"
            "- name: accuracy\n"
            "  command: python eval/bench.py\n"
            "  parse: json\n"
            "  weight: 0.5\n"
            "\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert len(config.project_eval) == 1
        assert config.project_eval[0].name == "accuracy"
        assert config.project_eval[0].command == "python eval/bench.py"
        assert config.project_eval[0].weight == 0.5

    def test_parses_multiple_dimensions(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Project Eval\n"
            "- name: accuracy\n"
            "  command: python bench.py\n"
            "  weight: 0.6\n"
            "- name: latency\n"
            "  command: python latency.py\n"
            "  parse: exit_code\n"
            "  weight: 0.4\n"
            "\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert len(config.project_eval) == 2
        assert config.project_eval[0].name == "accuracy"
        assert config.project_eval[1].name == "latency"
        assert config.project_eval[1].parse == "exit_code"

    def test_empty_section(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Project Eval\n\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.project_eval == []

    def test_skips_entries_without_name(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Project Eval\n"
            "- command: python bench.py\n"
            "  weight: 0.5\n"
            "\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.project_eval == []


class TestConfigParseEvalWeights:
    def test_parses_weights(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Eval Weights\n"
            "- hygiene: 0.25\n"
            "- growth: 0.25\n"
            "- project: 0.50\n"
            "\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.eval_weights.hygiene == 0.25
        assert config.eval_weights.growth == 0.25
        assert config.eval_weights.project == 0.50

    def test_defaults_without_section(self, tmp_path: Path) -> None:
        from factory.store import ExperimentStore

        md = (
            "## Goal\ntest\n\n"
            "## Eval\n### Command\n```bash\necho ok\n```\n### Threshold\n0.8\n"
        )
        (tmp_path / "factory.md").write_text(md)
        store = ExperimentStore(tmp_path)
        store.factory_dir.mkdir()
        config = asyncio.run(store.reparse_config())
        assert config.eval_weights.hygiene == 0.50
        assert config.eval_weights.growth == 0.50
        assert config.eval_weights.project == 0.0


# ── Eval runner tests ─────────────────────────────────────────────


class TestEffectiveWeights:
    def test_no_custom_project(self) -> None:
        from factory.eval.runner import _effective_weights

        h, g, p = _effective_weights(EvalWeights(), has_custom_project=False)
        assert h == 0.50
        assert g == 0.50
        assert p == 0.0

    def test_custom_with_explicit_weights(self) -> None:
        from factory.eval.runner import _effective_weights

        w = EvalWeights(hygiene=0.3, growth=0.2, project=0.5)
        h, g, p = _effective_weights(w, has_custom_project=True)
        assert abs(h - 0.30) < 1e-9
        assert abs(g - 0.20) < 1e-9
        assert abs(p - 0.50) < 1e-9

    def test_custom_without_explicit_weights(self) -> None:
        from factory.eval.runner import _effective_weights

        h, g, p = _effective_weights(EvalWeights(), has_custom_project=True)
        assert h == 0.30
        assert g == 0.20
        assert p == 0.50

    def test_weights_normalize(self) -> None:
        from factory.eval.runner import _effective_weights

        w = EvalWeights(hygiene=1.0, growth=1.0, project=2.0)
        h, g, p = _effective_weights(w, has_custom_project=True)
        assert abs(h + g + p - 1.0) < 1e-9
        assert abs(p - 0.50) < 1e-9


class TestNormalizeTier:
    def test_normalizes_weights(self) -> None:
        from factory.eval.runner import _normalize_tier

        results = [
            EvalResult(name="a", score=0.8, weight=2.0, passed=True, details=""),
            EvalResult(name="b", score=0.6, weight=3.0, passed=True, details=""),
        ]
        normalized = _normalize_tier(results, 0.50)
        total_weight = sum(r.weight for r in normalized)
        assert abs(total_weight - 0.50) < 1e-9

    def test_empty_returns_empty(self) -> None:
        from factory.eval.runner import _normalize_tier

        assert _normalize_tier([], 0.50) == []

    def test_zero_target_returns_empty(self) -> None:
        from factory.eval.runner import _normalize_tier

        results = [EvalResult(name="a", score=1.0, weight=1.0, passed=True, details="")]
        assert _normalize_tier(results, 0.0) == []


class TestMergeAllThreeWay:
    def test_two_way_default(self) -> None:
        from factory.eval.runner import _merge_all

        hygiene = [EvalResult(name="tests", score=0.9, weight=0.5, passed=True, details="")]
        growth = [EvalResult(name="cap", score=0.7, weight=0.5, passed=True, details="")]
        merged = _merge_all(hygiene, [], growth)
        total_weight = sum(r.weight for r in merged)
        assert abs(total_weight - 1.0) < 1e-9

    def test_three_way_with_project(self) -> None:
        from factory.eval.runner import _merge_all

        hygiene = [EvalResult(name="tests", score=0.9, weight=1.0, passed=True, details="")]
        growth = [EvalResult(name="cap", score=0.7, weight=1.0, passed=True, details="")]
        custom = [EvalResult(name="accuracy", score=0.85, weight=1.0, passed=True, details="")]
        weights = EvalWeights(hygiene=0.3, growth=0.2, project=0.5)
        merged = _merge_all(hygiene, [], growth, custom, weights)

        total_weight = sum(r.weight for r in merged)
        assert abs(total_weight - 1.0) < 1e-9

        # Check weight distribution
        hygiene_w = sum(r.weight for r in merged if r.name == "tests")
        growth_w = sum(r.weight for r in merged if r.name == "cap")
        project_w = sum(r.weight for r in merged if r.name == "accuracy")
        assert abs(hygiene_w - 0.30) < 1e-9
        assert abs(growth_w - 0.20) < 1e-9
        assert abs(project_w - 0.50) < 1e-9

    def test_three_way_auto_weights(self) -> None:
        from factory.eval.runner import _merge_all

        hygiene = [EvalResult(name="tests", score=0.9, weight=1.0, passed=True, details="")]
        growth = [EvalResult(name="cap", score=0.7, weight=1.0, passed=True, details="")]
        custom = [EvalResult(name="acc", score=0.85, weight=1.0, passed=True, details="")]
        merged = _merge_all(hygiene, [], growth, custom)

        # Auto weights: 30/20/50
        project_w = sum(r.weight for r in merged if r.name == "acc")
        assert abs(project_w - 0.50) < 1e-9


class TestRunSingleProjectDimension:
    def test_json_parse_success(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(
            name="bench", command="echo", parse="json", weight=0.5, timeout=10,
        )
        json_output = json.dumps({"score": 0.85, "details": "good"})
        with patch("factory.eval.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (json_output.encode(), b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.name == "bench"
            assert abs(result.score - 0.85) < 1e-9
            assert result.weight == 0.5

    def test_exit_code_parse(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(
            name="check", command="echo", parse="exit_code", weight=0.3,
        )
        with patch("factory.eval.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"ok", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.score == 1.0
            assert result.passed is True

    def test_exit_code_failure(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(
            name="check", command="false", parse="exit_code", weight=0.3,
        )
        with patch("factory.eval.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"failed")
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.score == 0.0
            assert result.passed is False

    def test_json_parse_invalid(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(
            name="bench", command="echo", parse="json", weight=0.5,
        )
        with patch("factory.eval.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"not json", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.score == 0.0
            assert "Invalid JSON" in result.details

    def test_command_not_found(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(
            name="missing", command="nonexistent_cmd", weight=0.5,
        )
        with patch(
            "factory.eval.runner.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.score == 0.0
            assert "not found" in result.details.lower()

    def test_score_clamped(self) -> None:
        from factory.eval.runner import _run_single_project_dimension

        dim = ProjectEvalDimension(name="x", command="echo", parse="json", weight=1.0)
        json_output = json.dumps({"score": 1.5})
        with patch("factory.eval.runner.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (json_output.encode(), b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = asyncio.run(_run_single_project_dimension(dim, Path(".")))
            assert result.score == 1.0


# ── Discovery tests ──────────────────────────────────────────────


class TestDetectProjectEvals:
    def test_finds_eval_scripts(self, tmp_path: Path) -> None:
        from factory.discovery.introspect import _detect_project_evals

        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "benchmark.py").write_text("print('bench')")
        (eval_dir / "score.py").write_text("# skip this")
        (eval_dir / "__init__.py").write_text("")

        evals = _detect_project_evals(tmp_path)
        assert len(evals) == 1
        assert evals[0]["name"] == "benchmark"

    def test_finds_root_scripts(self, tmp_path: Path) -> None:
        from factory.discovery.introspect import _detect_project_evals

        (tmp_path / "evaluate.py").write_text("print('eval')")
        (tmp_path / "benchmark.py").write_text("print('bench')")

        evals = _detect_project_evals(tmp_path)
        names = [e["name"] for e in evals]
        assert "evaluate" in names
        assert "benchmark" in names

    def test_finds_makefile_targets(self, tmp_path: Path) -> None:
        from factory.discovery.introspect import _detect_project_evals

        (tmp_path / "Makefile").write_text("all:\n\techo done\n\neval:\n\tpython eval.py\n")

        evals = _detect_project_evals(tmp_path)
        names = [e["name"] for e in evals]
        assert "eval" in names

    def test_empty_project(self, tmp_path: Path) -> None:
        from factory.discovery.introspect import _detect_project_evals

        evals = _detect_project_evals(tmp_path)
        assert evals == []

    def test_introspect_includes_discovered_evals(self, tmp_path: Path) -> None:
        from factory.discovery.introspect import introspect_project

        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "accuracy.py").write_text("print('acc')")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        profile = introspect_project(tmp_path)
        assert len(profile.discovered_evals) == 1
        assert profile.discovered_evals[0].name == "accuracy"


# ── CLI tests ─────────────────────────────────────────────────────


class TestBuildCeoTaskBranch:
    def test_no_branch(self) -> None:
        from factory.cli import _build_ceo_task

        task = _build_ceo_task(Path("/test"), "improve")
        assert "Branch Override" not in task

    def test_with_branch(self) -> None:
        from factory.cli import _build_ceo_task

        task = _build_ceo_task(Path("/test"), "improve", branch="factory/dev")
        assert "## Branch Override" in task
        assert "factory/dev" in task


class TestBuildCeoTaskProjectEval:
    def test_parser_accepts_skip_project_eval(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["eval", "/tmp/test", "--skip-project-eval"])
        assert args.skip_project_eval is True

    def test_parser_accepts_branch(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["ceo", "/tmp/test", "--branch", "dev"])
        assert args.branch == "dev"

    def test_run_parser_accepts_branch(self) -> None:
        from factory.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["run", "/tmp/test", "--branch", "staging"])
        assert args.branch == "staging"
