"""Tests for the reflection→mutation pipeline wiring (H1).

Phase 1: Plumbing fixes — individual_id propagation, OptKnob in seed workflow,
          mutate_knob logging, knob_values_by_id in _cmd_reflect.
Phase 2: Mutation selection quality — structured parsing, directional PARAM_MUTATE,
          EDGE_REDIRECT/SERIALIZE generation, archive_stats influence.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from unittest.mock import patch

from factory.cycle_analyzer import AgentStep, CycleRecord
from factory.outer_loop.models import MutationType
from factory.outer_loop.mutations import (
    WeightedRandomStrategy,
    _extract_param_direction,
    mutate_knob,
    parse_direction_from_suggestion,
    parse_operator_from_suggestion,
)
from factory.outer_loop.reflector import OuterLoopReflector, ReflectionReport
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Workflow,
)


def _make_step(
    role: str, succeeded: bool = True, error: str | None = None, duration: float = 10.0
) -> AgentStep:
    return AgentStep(
        order=0,
        role=role,
        started_at="2024-01-01T00:00:00",
        duration_s=duration,
        cost_usd=0.1,
        output_tokens=100,
        succeeded=succeeded,
        error=error,
    )


def _make_record(
    score: float,
    steps: list[AgentStep] | None = None,
    kept: int = 0,
    reverted: int = 0,
    errored: int = 0,
) -> CycleRecord:
    return CycleRecord(
        cycle_number=1,
        mode="test",
        started_at=None,
        ended_at=None,
        duration_s=10.0,
        score_start=0.0,
        score_end=score,
        score_delta=score,
        steps=steps or [],
        kept=kept,
        reverted=reverted,
        errored=errored,
    )


def _simple_workflow() -> Workflow:
    return Workflow(
        name="test",
        nodes={
            "builder": AgentNode(
                id="builder", role=AgentRole.BUILDER, timeout=600,
                writes={".factory/reviews/builder-latest.md"},
            ),
        },
        edges=[],
        start_node="builder",
        terminal=True,
    )


# ── Phase 1 Tests ──────────────────────────────────────────────────────


class TestPhase1aIndividualIdPropagation:
    def test_cmd_reflect_fallback_passes_individual_id(self, tmp_path: Path) -> None:
        """_cmd_reflect fallback branch should pass individual_id to evaluate."""
        import argparse

        from factory.outer_loop.evaluator import SwarmEvaluator
        from factory.outer_loop.models import EvalResult, SwarmConfig

        project = tmp_path
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        wf1 = Workflow(
            name="mode-a",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )
        wf2 = Workflow(
            name="mode-b",
            nodes={"b": AgentNode(id="b", role=AgentRole.RESEARCHER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        registry = EphemeralModeRegistry(project)
        registry.register("aaa", 0, wf1)
        registry.register("bbb", 0, wf2)

        cfg = SwarmConfig(benchmark="featurebench", budget=50)
        captured_ids: list[str | None] = []

        def spy_evaluate(self_ref: object, wf: object, proj: object, insts: object, individual_id: str | None = None) -> EvalResult:
            captured_ids.append(individual_id)
            return EvalResult(score=0.5 if "aaa" in (individual_id or "") else 0.3, benchmark_score=0.5)

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(SwarmEvaluator, "evaluate", spy_evaluate), \
             patch.object(SwarmEvaluator, "get_cycle_record", return_value=_make_record(0.5, kept=1)):
            from factory.cli.outer_loop import _cmd_reflect

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_reflect(ns)
            assert rc == 0

        for cid in captured_ids:
            assert cid is not None, "individual_id should not be None in fallback branch"


class TestPhase1bOptKnobInSeedWorkflow:
    def test_featurebench_workflow_has_knobs(self) -> None:
        from factory.workflow.contributed.featurebench.workflow import workflow

        wf = workflow()
        assert "agent_timeout" in wf.knob_values
        assert "agent_model" in wf.knob_values
        assert wf.knob_values["agent_timeout"] == 7200
        assert wf.knob_values["agent_model"] == "opus"

    def test_featurebench_workflow_knob_bounds(self) -> None:
        from factory.workflow.contributed.featurebench.workflow import workflow

        wf = workflow()
        assert "agent_timeout" in wf.knob_bounds
        assert "agent_model" in wf.knob_bounds
        assert 300 in wf.knob_bounds["agent_timeout"]
        assert 7200 in wf.knob_bounds["agent_timeout"]
        assert "sonnet" in wf.knob_bounds["agent_model"]
        assert "opus" in wf.knob_bounds["agent_model"]
        assert "haiku" in wf.knob_bounds["agent_model"]

    def test_featurebench_workflow_knob_expandable(self) -> None:
        from factory.workflow.contributed.featurebench.workflow import workflow

        wf = workflow()
        assert "agent_timeout" in wf.knob_expandable

    def test_inline_seed_has_knobs(self) -> None:
        """The inline featurebench seed in _cmd_calibrate should also have knobs."""
        from factory.workflow.primitives import AgentNode, AgentRole, Workflow

        wf = Workflow(
            name="featurebench-seed",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    model="opus",
                    timeout=7200,
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
            terminal=True,
            knob_values={"agent_timeout": 7200, "agent_model": "opus"},
            knob_bounds={
                "agent_timeout": [300, 600, 900, 1200, 1800, 3600, 7200],
                "agent_model": ["sonnet", "opus", "haiku"],
            },
            knob_expandable={"agent_timeout": "Agent timeout in seconds for the builder node"},
        )
        assert wf.knob_values["agent_timeout"] == 7200
        assert len(wf.knob_bounds["agent_timeout"]) == 7

    def test_knob_mutate_fires_with_seeded_knobs(self) -> None:
        """KNOB_MUTATE should be able to mutate the seed workflow's knobs."""
        from factory.workflow.contributed.featurebench.workflow import workflow

        wf = workflow()
        result = mutate_knob(wf, expander=None)
        assert result is not None
        child_wf, rec = result
        assert rec.operator == MutationType.KNOB_MUTATE


class TestPhase1dMutateKnobLogging:
    def test_empty_knob_values_logs_skip(self, caplog: object) -> None:
        wf = _simple_workflow()
        assert not wf.knob_values

        with patch("factory.outer_loop.mutations.log") as mock_log:
            result = mutate_knob(wf, expander=None)
            assert result is None
            mock_log.info.assert_called_once_with(
                "knob_mutate_skipped", reason="empty_knob_values"
            )


class TestPhase1ReflectPassesKnobValues:
    def test_cmd_reflect_passes_knob_values_by_id(self, tmp_path: Path) -> None:
        """_cmd_reflect should build and pass knob_values_by_id to reflector."""
        import argparse

        from factory.outer_loop.models import SwarmConfig

        project = tmp_path
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        results_dir = project / ".factory" / "outer_loop" / "results"
        results_dir.mkdir(parents=True)

        wf = Workflow(
            name="mode-a",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
            knob_values={"agent_timeout": 600, "agent_model": "opus"},
        )
        wf2 = Workflow(
            name="mode-b",
            nodes={"b": AgentNode(id="b", role=AgentRole.RESEARCHER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        name_a = registry.register("aaa", 0, wf)
        name_b = registry.register("bbb", 0, wf2)

        gen_results = {
            name_a: {"score": 0.85, "cost_usd": 1.0},
            name_b: {"score": 0.72, "cost_usd": 0.5},
        }
        (results_dir / "gen0.json").write_text(json.dumps(gen_results))

        for name, score in [(name_a, 0.85), (name_b, 0.72)]:
            runs_dir = project / ".factory" / "outer_loop" / "runs" / name
            runs_dir.mkdir(parents=True)
            summary = {"mode": name, "score": score, "cost_usd": 0.5, "kept": 2, "reverted": 1}
            (runs_dir / "cycle_summary.json").write_text(json.dumps(summary))

        cfg = SwarmConfig(benchmark="featurebench", budget=50)

        captured_kwargs: list[dict[str, object]] = []
        original_reflect = OuterLoopReflector.reflect

        def spy_reflect(self_ref: object, records: object, generation: int = 0, **kwargs: object) -> object:
            captured_kwargs.append(kwargs)
            return original_reflect(self_ref, records, generation, **kwargs)  # type: ignore[arg-type]

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch.object(OuterLoopReflector, "reflect", spy_reflect):
            from factory.cli.outer_loop import _cmd_reflect

            ns = argparse.Namespace(project_path=str(project), generation=0)
            rc = _cmd_reflect(ns)
            assert rc == 0

        assert len(captured_kwargs) == 1
        knob_vals = captured_kwargs[0].get("knob_values_by_id")
        assert knob_vals is not None
        assert name_a in knob_vals  # type: ignore[operator]
        assert knob_vals[name_a]["agent_timeout"] == 600  # type: ignore[index]


# ── Phase 2 Tests ──────────────────────────────────────────────────────


class TestPhase2aStructuredParsing:
    def test_parse_prefix_format(self) -> None:
        assert parse_operator_from_suggestion("NODE_INSERT: Add researcher") == MutationType.NODE_INSERT
        assert parse_operator_from_suggestion("NODE_REMOVE: Remove builder") == MutationType.NODE_REMOVE
        assert parse_operator_from_suggestion("EDGE_REDIRECT: Reroute output") == MutationType.EDGE_REDIRECT
        assert parse_operator_from_suggestion("SERIALIZE: Enforce ordering") == MutationType.SERIALIZE
        assert parse_operator_from_suggestion("PARALLELIZE: Run in parallel") == MutationType.PARALLELIZE
        assert parse_operator_from_suggestion("PARAM_MUTATE: Increase timeout") == MutationType.PARAM_MUTATE
        assert parse_operator_from_suggestion("PROMPT_MUTATE: Improve prompt") == MutationType.PROMPT_MUTATE
        assert parse_operator_from_suggestion("KNOB_MUTATE: agent_timeout=900") == MutationType.KNOB_MUTATE

    def test_parse_substring_format(self) -> None:
        assert parse_operator_from_suggestion("Consider PARALLELIZE for agents") == MutationType.PARALLELIZE
        assert parse_operator_from_suggestion("Try EDGE_REDIRECT to improve flow") == MutationType.EDGE_REDIRECT

    def test_parse_returns_none_for_unrecognized(self) -> None:
        assert parse_operator_from_suggestion("Just a random suggestion") is None
        assert parse_operator_from_suggestion("") is None

    def test_guided_operator_selects_edge_redirect(self) -> None:
        strategy = WeightedRandomStrategy()
        report = ReflectionReport(
            mutation_suggestions=["EDGE_REDIRECT: Reroute output from failing agent"],
        )
        wf = _simple_workflow()

        results: set[MutationType] = set()
        for _ in range(50):
            op = strategy.select_guided_operator(wf, 0, report)
            results.add(op)

        assert MutationType.EDGE_REDIRECT in results

    def test_guided_operator_selects_serialize(self) -> None:
        strategy = WeightedRandomStrategy()
        report = ReflectionReport(
            structural_recommendations=["SERIALIZE: Enforce sequential order"],
        )
        wf = _simple_workflow()

        results: set[MutationType] = set()
        for _ in range(50):
            op = strategy.select_guided_operator(wf, 0, report)
            results.add(op)

        assert MutationType.SERIALIZE in results

    def test_all_8_operators_reachable(self) -> None:
        """All MutationType enum values should be parseable from suggestions."""
        for mt in MutationType:
            suggestion = f"{mt.value}: Test suggestion"
            parsed = parse_operator_from_suggestion(suggestion)
            assert parsed == mt, f"Failed to parse {mt.value}"


class TestPhase2bDirectionalParamMutate:
    def test_parse_direction_increase(self) -> None:
        assert parse_direction_from_suggestion("Increase timeout for builder") == "increase"

    def test_parse_direction_decrease(self) -> None:
        assert parse_direction_from_suggestion("Decrease timeout to save cost") == "decrease"
        assert parse_direction_from_suggestion("Reduce timeout for faster runs") == "decrease"
        assert parse_direction_from_suggestion("Lower the timeout value") == "decrease"

    def test_parse_direction_none(self) -> None:
        assert parse_direction_from_suggestion("Change timeout for builder") is None

    def test_extract_param_direction_from_report(self) -> None:
        report = ReflectionReport(
            structural_recommendations=[
                "PARAM_MUTATE: Increase timeout for agents that timed out (builder)"
            ],
        )
        direction = _extract_param_direction(report, "builder")
        assert direction == "increase"

    def test_extract_param_direction_none_without_report(self) -> None:
        assert _extract_param_direction(None, "builder") is None
        assert _extract_param_direction("not a report", "builder") is None


class TestPhase2cReflectorEdgeRedirectSerialize:
    def test_edge_redirect_emitted_for_mixed_success(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=1)),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("researcher", succeeded=True), _make_step("builder", succeeded=False, error="crash")],
                errored=1,
            )),
        ]
        report = reflector.reflect(records, generation=0)
        edge_recs = [r for r in report.structural_recommendations if "EDGE_REDIRECT" in r]
        assert len(edge_recs) > 0

    def test_serialize_emitted_for_ordering_dependency(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=1)),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("researcher", succeeded=True), _make_step("builder", succeeded=False, error="dep")],
                errored=1,
            )),
        ]
        report = reflector.reflect(records, generation=0)
        ser_recs = [r for r in report.structural_recommendations if "SERIALIZE" in r]
        assert len(ser_recs) > 0

    def test_no_edge_redirect_when_all_succeed(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder", succeeded=True)], kept=1)),
            ("l1", 0.3, _make_record(0.3, [_make_step("builder", succeeded=True)], kept=0)),
        ]
        report = reflector.reflect(records, generation=0)
        edge_recs = [r for r in report.structural_recommendations if "EDGE_REDIRECT" in r]
        assert len(edge_recs) == 0


class TestPhase2dArchiveStatsInfluence:
    def test_low_diversity_boosts_structural_operators(self) -> None:
        strategy = WeightedRandomStrategy()
        wf = _simple_workflow()

        random.seed(42)
        counts_normal: dict[MutationType, int] = {}
        for _ in range(1000):
            op = strategy.select_operator(wf, 0, {})
            counts_normal[op] = counts_normal.get(op, 0) + 1

        random.seed(42)
        counts_boosted: dict[MutationType, int] = {}
        for _ in range(1000):
            op = strategy.select_operator(wf, 0, {"diversity": 0.1})
            counts_boosted[op] = counts_boosted.get(op, 0) + 1

        structural = {MutationType.NODE_INSERT, MutationType.PARALLELIZE, MutationType.EDGE_REDIRECT}
        structural_normal = sum(counts_normal.get(t, 0) for t in structural)
        structural_boosted = sum(counts_boosted.get(t, 0) for t in structural)
        assert structural_boosted > structural_normal

    def test_high_diversity_no_boost(self) -> None:
        strategy = WeightedRandomStrategy()
        wf = _simple_workflow()

        random.seed(42)
        counts_high_div: dict[MutationType, int] = {}
        for _ in range(1000):
            op = strategy.select_operator(wf, 0, {"diversity": 0.8})
            counts_high_div[op] = counts_high_div.get(op, 0) + 1

        random.seed(42)
        counts_no_stats: dict[MutationType, int] = {}
        for _ in range(1000):
            op = strategy.select_operator(wf, 0, {})
            counts_no_stats[op] = counts_no_stats.get(op, 0) + 1

        assert counts_high_div == counts_no_stats


class TestPhase2PromptImprovements:
    def test_knob_patterns_populate_prompt_improvements(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=1)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder")], kept=0)),
        ]
        knob_values_by_id = {
            "w1": {"_prompt_builder": "Think step by step"},
            "l1": {"_prompt_builder": "Be creative"},
        }
        report = reflector.reflect(records, generation=0, knob_values_by_id=knob_values_by_id)
        assert len(report.prompt_improvements) > 0
        assert any("prompt" in p.lower() or "strategy" in p.lower() for p in report.prompt_improvements)


class TestEvolveLoadsReflection:
    def test_evolve_loads_persisted_reflection_json(self, tmp_path: Path) -> None:
        import argparse

        from factory.outer_loop.models import SwarmConfig

        project = tmp_path
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)
        reflect_dir = project / ".factory" / "outer_loop" / "reflections"
        reflect_dir.mkdir(parents=True)

        wf = Workflow(
            name="wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("seed01", 0, wf)

        reflection_data = {
            "failure_patterns": ["Agent failed"],
            "success_patterns": ["Agent succeeded"],
            "mutation_suggestions": ["NODE_INSERT: Add researcher"],
            "prompt_improvements": [],
            "structural_recommendations": [],
            "top_k_ids": ["w1"],
            "bottom_k_ids": ["l1"],
        }
        (reflect_dir / "gen0.json").write_text(json.dumps(reflection_data))

        cfg = SwarmConfig(benchmark="featurebench", budget=50, population_size=1)

        captured_kwargs: list[dict[str, object]] = []

        def spy_apply(wf: object, strategy: object, gen: int, **kwargs: object) -> object:
            captured_kwargs.append(kwargs)
            return None

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.mutations.apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        ref = captured_kwargs[0].get("reflection_report")
        assert ref is not None
        assert len(ref.mutation_suggestions) == 1  # type: ignore[union-attr]

    def test_evolve_works_without_reflection_file(self, tmp_path: Path) -> None:
        import argparse

        from factory.outer_loop.models import SwarmConfig

        project = tmp_path
        modes_dir = project / ".factory" / "outer_loop" / "modes"
        modes_dir.mkdir(parents=True)

        wf = Workflow(
            name="wf",
            nodes={"b": AgentNode(id="b", role=AgentRole.BUILDER, writes=set())},
            edges=[], start_node="b", terminal=True,
        )

        from factory.outer_loop.mode_registry import EphemeralModeRegistry

        registry = EphemeralModeRegistry(project)
        registry.register("seed01", 0, wf)

        cfg = SwarmConfig(benchmark="featurebench", budget=50, population_size=1)
        captured_kwargs: list[dict[str, object]] = []

        def spy_apply(wf: object, strategy: object, gen: int, **kwargs: object) -> object:
            captured_kwargs.append(kwargs)
            return None

        with patch("factory.outer_loop.filesystem.load_config", return_value=cfg), \
             patch("factory.outer_loop.mutations.apply_random_mutation", side_effect=spy_apply):
            from factory.cli.outer_loop import _cmd_evolve

            ns = argparse.Namespace(project_path=str(project), generation=0)
            _cmd_evolve(ns)

        assert len(captured_kwargs) > 0
        ref = captured_kwargs[0].get("reflection_report")
        assert ref is None
