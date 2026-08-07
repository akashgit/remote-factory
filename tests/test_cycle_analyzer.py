"""Tests for CycleAnalyzer and InnerLoop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory.cycle_analyzer import CycleAnalyzer, CycleRecord
from factory.inner_loop import (
    CirclePackingEvaluator,
    Evaluator,
    InnerLoop,
)


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture()
def factory_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".factory"
    d.mkdir()
    return d


def _write_events(factory_dir: Path, events: list[dict]) -> None:
    (factory_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )


def _write_results_tsv(factory_dir: Path, rows: list[dict]) -> None:
    cols = [
        "id", "timestamp", "hypothesis", "change_summary", "issue_number",
        "pr_number", "score_before", "score_after", "delta", "verdict",
        "cost_usd", "notes", "research_citations",
    ]
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(str(row.get(c, "")) for c in cols))
    (factory_dir / "results.tsv").write_text("\n".join(lines) + "\n")


def _make_events(
    *,
    n_experiments: int = 2,
    verdicts: list[str] | None = None,
    scores: list[float] | None = None,
    agent_costs: list[float] | None = None,
) -> list[dict]:
    if verdicts is None:
        verdicts = ["keep"] * n_experiments
    if scores is None:
        scores = [0.5 + 0.1 * i for i in range(n_experiments)]
    if agent_costs is None:
        agent_costs = [1.0] * n_experiments

    events: list[dict] = []

    for i in range(n_experiments):
        minute = i * 15
        events.append({
            "type": "experiment.begin",
            "timestamp": f"2026-07-22T10:{minute:02d}:00+00:00",
            "project": "test",
            "agent": None,
            "data": {"exp_id": i + 1, "hypothesis": f"hypothesis {i + 1}"},
        })
        events.append({
            "type": "agent.started",
            "timestamp": f"2026-07-22T10:{minute:02d}:01+00:00",
            "project": "test",
            "agent": "builder",
            "data": {},
        })
        events.append({
            "type": "agent.completed",
            "timestamp": f"2026-07-22T10:{minute + 5:02d}:00+00:00",
            "project": "test",
            "agent": "builder",
            "data": {
                "return_code": 0,
                "total_cost_usd": agent_costs[i],
                "output_tokens": 1000,
                "duration_ms": 300000,
            },
        })
        events.append({
            "type": "eval.completed",
            "timestamp": f"2026-07-22T10:{minute + 6:02d}:00+00:00",
            "project": "test",
            "agent": None,
            "data": {"composite": scores[i], "passed": True},
        })
        events.append({
            "type": "experiment.finalize",
            "timestamp": f"2026-07-22T10:{minute + 7:02d}:00+00:00",
            "project": "test",
            "agent": None,
            "data": {
                "exp_id": i + 1,
                "verdict": verdicts[i],
                "hypothesis": f"hypothesis {i + 1}",
            },
        })
    return events


# ── CycleAnalyzer Tests ──────────────────────────────────────


class TestCycleAnalyzerEmpty:
    def test_empty_factory_dir(self, factory_dir: Path) -> None:
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.experiments == []
        assert r.score_trajectory == []
        assert r.total_cost_usd == 0.0

    def test_no_events_file(self, factory_dir: Path) -> None:
        a = CycleAnalyzer(factory_dir)
        assert a.trajectory() == []

    def test_empty_events_file(self, factory_dir: Path) -> None:
        (factory_dir / "events.jsonl").write_text("")
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.steps == []


class TestCycleAnalyzerParseEvents:
    def test_skips_malformed_json(self, factory_dir: Path) -> None:
        (factory_dir / "events.jsonl").write_text(
            "not json\n"
            '{"type": "detect", "timestamp": "2026-07-22T10:00:00Z", "data": {}}\n'
            "{invalid}\n"
        )
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None

    def test_skips_schema_invalid_events(self, factory_dir: Path) -> None:
        (factory_dir / "events.jsonl").write_text(
            json.dumps({"no_type": True, "timestamp": "2026-07-22T10:00:00Z"}) + "\n"
            + json.dumps({"type": "test", "no_timestamp": True}) + "\n"
            + json.dumps({"type": "detect", "timestamp": "2026-07-22T10:00:00Z", "data": {}}) + "\n"
        )
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None

    def test_extracts_experiments(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=2, verdicts=["keep", "revert"])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.experiments) == 2
        assert r.experiments[0].verdict == "keep"
        assert r.experiments[1].verdict == "revert"

    def test_extracts_agent_steps(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1)
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.steps) == 1
        assert r.steps[0].role == "builder"
        assert r.steps[0].succeeded is True

    def test_extracts_failed_agent(self, factory_dir: Path) -> None:
        events = [
            {"type": "agent.started", "timestamp": "2026-07-22T10:00:00+00:00",
             "project": "test", "agent": "builder", "data": {}},
            {"type": "agent.failed", "timestamp": "2026-07-22T10:05:00+00:00",
             "project": "test", "agent": "builder",
             "data": {"return_code": 1, "stderr": "timed out"}},
        ]
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.steps) == 1
        assert r.steps[0].succeeded is False
        assert r.steps[0].error == "timed out"

    def test_extracts_scores(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=3, scores=[0.5, 0.7, 0.9])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.score_trajectory == [0.5, 0.7, 0.9]
        assert r.score_start == 0.5
        assert r.score_end == 0.9

    def test_computes_cost(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=2, agent_costs=[1.5, 2.5])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.total_cost_usd == 4.0
        assert r.cost_by_agent == {"builder": 4.0}

    def test_computes_experiment_cost(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1, agent_costs=[3.0])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.experiments[0].cost_usd == 3.0


class TestCycleAnalyzerResultsTsv:
    def test_enriches_from_tsv(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1, verdicts=["keep"])
        _write_events(factory_dir, events)
        _write_results_tsv(factory_dir, [
            {"id": "1", "hypothesis": "better hypothesis", "score_before": "0.3",
             "score_after": "0.5", "verdict": "keep"},
        ])
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.experiments[0].score_before == 0.3
        assert r.experiments[0].score_after == 0.5
        assert r.experiments[0].score_delta == pytest.approx(0.2)

    def test_adds_missing_experiments(self, factory_dir: Path) -> None:
        _write_results_tsv(factory_dir, [
            {"id": "1", "hypothesis": "h1", "score_before": "0.3",
             "score_after": "0.5", "verdict": "keep"},
            {"id": "2", "hypothesis": "h2", "score_before": "0.5",
             "score_after": "0.4", "verdict": "revert"},
        ])
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.experiments) == 2
        assert r.kept == 1
        assert r.reverted == 1

    def test_tsv_scores_override_events(self, factory_dir: Path) -> None:
        _write_results_tsv(factory_dir, [
            {"id": "1", "score_after": "0.5", "verdict": "keep"},
            {"id": "2", "score_after": "0.8", "verdict": "keep"},
            {"id": "3", "score_after": "1.0", "verdict": "keep"},
        ])
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.score_trajectory == [0.5, 0.8, 1.0]


class TestCycleAnalyzerEvalArtifacts:
    def test_discovers_eval_files(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1, verdicts=["keep"])
        _write_events(factory_dir, events)
        exp_dir = factory_dir / "experiments" / "1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "eval_after.json").write_text('{"combined_score": 0.85}')
        (exp_dir / "candidate.py").write_text("print('hello')")
        (exp_dir / "hypothesis.md").write_text("test")

        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        artifacts = r.experiments[0].eval_artifacts
        assert any("eval_after.json" in a for a in artifacts)
        assert any("candidate.py" in a for a in artifacts)
        assert not any("hypothesis.md" in a for a in artifacts)

    def test_discovers_zero_padded_dirs(self, factory_dir: Path) -> None:
        _write_results_tsv(factory_dir, [
            {"id": "1", "verdict": "keep"},
        ])
        exp_dir = factory_dir / "experiments" / "001"
        exp_dir.mkdir(parents=True)
        (exp_dir / "eval_after.json").write_text("{}")

        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.experiments[0].eval_artifacts) == 1


class TestCycleAnalyzerConvergence:
    def test_consecutive_reverts(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=3, verdicts=["keep", "revert", "revert"])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.consecutive_reverts == 2

    def test_no_trailing_reverts(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=2, verdicts=["revert", "keep"])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.consecutive_reverts == 0

    def test_keep_rate(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=4, verdicts=["keep", "revert", "keep", "revert"])
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.keep_rate == 0.5


class TestCycleAnalyzerApi:
    def test_trajectory(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=2, scores=[0.5, 0.8])
        _write_events(factory_dir, events)
        assert CycleAnalyzer(factory_dir).trajectory() == [0.5, 0.8]

    def test_to_jsonl(self, factory_dir: Path, tmp_path: Path) -> None:
        events = _make_events(n_experiments=1)
        _write_events(factory_dir, events)
        out = tmp_path / "cycles.jsonl"
        CycleAnalyzer(factory_dir).to_jsonl(out)
        CycleAnalyzer(factory_dir).to_jsonl(out)
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        d = json.loads(lines[0])
        assert "cycle_number" in d
        assert "score_trajectory" in d

    def test_duration(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1)
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.duration_s > 0


class TestCycleAnalyzerDagMapping:
    def test_node_trace_with_workflow(self, factory_dir: Path) -> None:
        from factory.workflow.definitions import evolve_workflow
        events = [
            {"type": "agent.started", "timestamp": "2026-07-22T10:00:00+00:00",
             "project": "test", "agent": "researcher", "data": {}},
            {"type": "agent.completed", "timestamp": "2026-07-22T10:05:00+00:00",
             "project": "test", "agent": "researcher",
             "data": {"return_code": 0, "total_cost_usd": 1.0}},
        ]
        _write_events(factory_dir, events)
        wf = evolve_workflow()
        r = CycleAnalyzer(factory_dir, workflow=wf).latest()
        assert r is not None
        assert len(r.node_trace) > 0
        assert "researcher" in r.node_trace
        assert r.node_trace["researcher"].role == "researcher"
        assert r.node_trace["researcher"].event is not None
        assert r.node_trace["researcher"].event["cost_usd"] == 1.0

    def test_node_trace_without_workflow(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=1)
        _write_events(factory_dir, events)
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.node_trace == {}

    def test_agent_step_maps_to_node(self, factory_dir: Path) -> None:
        from factory.workflow.definitions import evolve_workflow
        events = [
            {"type": "agent.started", "timestamp": "2026-07-22T10:00:00+00:00",
             "project": "test", "agent": "builder", "data": {}},
            {"type": "agent.completed", "timestamp": "2026-07-22T10:05:00+00:00",
             "project": "test", "agent": "builder",
             "data": {"return_code": 0, "total_cost_usd": 2.0}},
        ]
        _write_events(factory_dir, events)
        wf = evolve_workflow()
        r = CycleAnalyzer(factory_dir, workflow=wf).latest()
        assert r is not None
        assert r.steps[0].node_id == "builder"
        assert len(r.steps[0].produced) > 0


# ── CirclePackingEvaluator Tests ──────────────────────────────


class TestCirclePackingEvaluator:
    def test_parse_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "eval.json"
        f.write_text(json.dumps({
            "sum_radii": 2.1, "target_ratio": 0.8,
            "validity": 1.0, "eval_time": 1.5, "combined_score": 0.8,
        }))
        r = CirclePackingEvaluator().parse(f)
        assert r.score == 0.8
        assert r.valid is True
        assert r.metrics["sum_radii"] == 2.1

    def test_parse_invalid_validity(self, tmp_path: Path) -> None:
        f = tmp_path / "eval.json"
        f.write_text(json.dumps({"validity": 0.0, "combined_score": 0.3}))
        r = CirclePackingEvaluator().parse(f)
        assert r.valid is False

    def test_parse_missing_file(self) -> None:
        r = CirclePackingEvaluator().parse(Path("/nonexistent/file.json"))
        assert r.score == 0.0
        assert r.valid is False

    def test_parse_malformed_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json")
        r = CirclePackingEvaluator().parse(f)
        assert r.score == 0.0
        assert r.valid is False

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.json"
        f.write_text("")
        r = CirclePackingEvaluator().parse(f)
        assert r.score == 0.0

    def test_parse_many_picks_best(self, tmp_path: Path) -> None:
        for i, score in enumerate([0.3, 0.9, 0.6]):
            f = tmp_path / f"eval_{i}.json"
            f.write_text(json.dumps({"combined_score": score, "validity": 1.0}))
        files = [tmp_path / f"eval_{i}.json" for i in range(3)]
        r = CirclePackingEvaluator().parse_many(files)
        assert r.score == 0.9

    def test_parse_many_empty_list(self) -> None:
        r = CirclePackingEvaluator().parse_many([])
        assert r.score == 0.0
        assert r.valid is False

    def test_parse_many_all_invalid(self, tmp_path: Path) -> None:
        for i in range(2):
            f = tmp_path / f"bad_{i}.json"
            f.write_text("not json")
        files = [tmp_path / f"bad_{i}.json" for i in range(2)]
        r = CirclePackingEvaluator().parse_many(files)
        assert r.score == 0.0

    def test_satisfies_evaluator_protocol(self) -> None:
        assert isinstance(CirclePackingEvaluator(), Evaluator)

    def test_get_info(self) -> None:
        info = CirclePackingEvaluator(target=3.0).get_info()
        assert info["benchmark"] == "circle_packing"
        assert info["target"] == 3.0


# ── InnerLoop Tests ──────────────────────────────────────────


class TestInnerLoopCollect:
    def test_collect_empty(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        r = loop.collect()
        assert r.mode == "evolve"
        assert r.experiments == []

    def test_collect_with_data(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        fd = proj / ".factory"
        fd.mkdir()
        _write_results_tsv(fd, [
            {"id": "1", "hypothesis": "h1", "score_before": "0.3",
             "score_after": "0.5", "verdict": "keep"},
        ])
        loop = InnerLoop(proj, mode="evolve")
        r = loop.collect()
        assert r.mode == "evolve"
        assert len(r.experiments) == 1
        assert r.experiments[0].score_after == 0.5

    def test_collect_with_evaluator(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        fd = proj / ".factory"
        fd.mkdir()
        events = _make_events(n_experiments=1, verdicts=["keep"])
        _write_events(fd, events)
        exp_dir = fd / "experiments" / "1"
        exp_dir.mkdir(parents=True)
        (exp_dir / "eval_after.json").write_text(json.dumps({
            "combined_score": 0.85, "validity": 1.0, "sum_radii": 2.1,
        }))

        evaluator = CirclePackingEvaluator()
        loop = InnerLoop(proj, mode="evolve", evaluator=evaluator)
        r = loop.collect()
        assert r.experiments[0].score_after == 0.85
        assert r.score_end == 0.85


class TestInnerLoopMethods:
    def test_score_trajectory_empty(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        assert loop.score_trajectory() == []

    def test_total_cost_empty(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        assert loop.total_cost() == 0.0

    def test_history_empty(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        assert loop.history() == []


class TestInnerLoopDirectives:
    def test_write_directives(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        loop._write_directives({
            "prefer_categories": ["algorithm-change"],
            "target_score": 1.0,
        })
        msg_dir = proj / ".factory" / "messages"
        assert msg_dir.exists()
        files = list(msg_dir.iterdir())
        assert len(files) == 1
        content = files[0].read_text()
        assert "algorithm-change" in content
        assert "target_score" in content

    def test_write_directives_increments(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        loop._write_directives({"a": 1})
        loop._step_count = 1
        loop._write_directives({"b": 2})
        msg_dir = proj / ".factory" / "messages"
        assert len(list(msg_dir.iterdir())) == 2


class TestInnerLoopModePropagate:
    def test_mode_set_without_workflow(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="research")
        r = loop.collect()
        assert r.mode == "research"


# ── Executor Protocol Tests ──────────────────────────────────


class TestExecutorProtocol:
    def test_factory_ceo_executor_satisfies_protocol(self) -> None:
        from factory.inner_loop import Executor, FactoryCeoExecutor
        executor = FactoryCeoExecutor(mode="evolve")
        assert isinstance(executor, Executor)

    def test_bare_class_satisfies_protocol(self) -> None:
        from factory.inner_loop import Executor

        class MyExecutor:
            def execute(
                self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
            ) -> CycleRecord:
                return CycleRecord(
                    cycle_number=0, mode="test", started_at=None, ended_at=None,
                    duration_s=0, score_start=None, score_end=None, score_delta=None,
                )

        assert isinstance(MyExecutor(), Executor)


class TestInnerLoopCustomExecutor:
    def test_step_delegates_to_custom_executor(self, tmp_path: Path) -> None:
        class StubExecutor:
            def __init__(self) -> None:
                self.calls: list[tuple[Path, dict[str, Any] | None]] = []

            def execute(
                self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
            ) -> CycleRecord:
                self.calls.append((project_dir, directives))
                return CycleRecord(
                    cycle_number=0, mode="stub", started_at=None, ended_at=None,
                    duration_s=1.0, score_start=0.0, score_end=0.75, score_delta=0.75,
                    item_results=[{"id": "item-1", "score": 0.75}],
                )

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        executor = StubExecutor()
        loop = InnerLoop(proj, executor=executor)

        record = loop.step(directives={"focus": "test"})

        assert len(executor.calls) == 1
        assert executor.calls[0][1] == {"focus": "test"}
        assert record.cycle_number == 1
        assert record.score_end == 0.75
        assert record.item_results == [{"id": "item-1", "score": 0.75}]
        assert len(loop.history()) == 1

    def test_cycle_number_increments(self, tmp_path: Path) -> None:
        class FixedExecutor:
            def execute(
                self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
            ) -> CycleRecord:
                return CycleRecord(
                    cycle_number=0, mode="fixed", started_at=None, ended_at=None,
                    duration_s=0, score_start=None, score_end=0.5, score_delta=None,
                )

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, executor=FixedExecutor())

        r1 = loop.step()
        r2 = loop.step()
        assert r1.cycle_number == 1
        assert r2.cycle_number == 2


class TestCycleRecordItemResults:
    def test_default_empty(self) -> None:
        from factory.cycle_analyzer import CycleRecord
        r = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=0, score_start=None, score_end=None, score_delta=None,
        )
        assert r.item_results == []

    def test_construct_with_item_results(self) -> None:
        from factory.cycle_analyzer import CycleRecord
        items = [
            {"id": "q1", "hard": 1.0, "soft": 0.9, "fail_reason": ""},
            {"id": "q2", "hard": 0.0, "soft": 0.1, "fail_reason": "wrong_answer"},
        ]
        r = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=0, score_start=None, score_end=None, score_delta=None,
            item_results=items,
        )
        assert len(r.item_results) == 2
        assert r.item_results[0]["id"] == "q1"
        assert r.item_results[1]["fail_reason"] == "wrong_answer"

    def test_to_jsonl_serializes_item_results(self, tmp_path: Path) -> None:
        from factory.cycle_analyzer import CycleRecord
        from dataclasses import asdict
        items = [{"id": "x", "hard": 1.0}]
        r = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=0, score_start=None, score_end=None, score_delta=None,
            item_results=items,
        )
        d = asdict(r)
        d.pop("node_trace", None)
        d.pop("steps", None)
        serialized = json.dumps(d, default=str)
        parsed = json.loads(serialized)
        assert parsed["item_results"] == [{"id": "x", "hard": 1.0}]

    def test_item_results_round_trip(self) -> None:
        from factory.cycle_analyzer import CycleRecord
        from dataclasses import asdict
        items = [
            {"id": "a", "hard": 1.0, "soft": 0.8, "fail_reason": "", "extras": {"pred": "Paris"}},
            {"id": "b", "hard": 0.0, "soft": 0.2, "fail_reason": "timeout", "extras": {}},
        ]
        r = CycleRecord(
            cycle_number=1, mode="test", started_at=None, ended_at=None,
            duration_s=0, score_start=None, score_end=None, score_delta=None,
            item_results=items,
        )
        d = asdict(r)
        serialized = json.dumps(d, default=str)
        parsed = json.loads(serialized)
        assert parsed["item_results"] == items


class TestInnerLoopBackwardCompat:
    def test_default_executor_is_factory_ceo(self, tmp_path: Path) -> None:
        from factory.inner_loop import FactoryCeoExecutor
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        assert isinstance(loop.executor, FactoryCeoExecutor)

    def test_default_executor_inherits_mode(self, tmp_path: Path) -> None:
        from factory.inner_loop import FactoryCeoExecutor
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="research")
        assert isinstance(loop.executor, FactoryCeoExecutor)
        assert loop.executor.mode == "research"

    def test_collect_still_works(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        fd = proj / ".factory"
        fd.mkdir()
        _write_results_tsv(fd, [
            {"id": "1", "hypothesis": "h1", "score_before": "0.3",
             "score_after": "0.5", "verdict": "keep"},
        ])
        loop = InnerLoop(proj, mode="evolve")
        r = loop.collect()
        assert r.mode == "evolve"
        assert len(r.experiments) == 1


class TestFactoryCeoExecutorDirectives:
    def test_writes_markdown_directives(self, tmp_path: Path) -> None:
        from factory.inner_loop import FactoryCeoExecutor
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        executor = FactoryCeoExecutor(mode="evolve")
        executor._write_directives(factory_dir, {
            "prefer_categories": ["algorithm-change"],
            "target_score": 1.0,
        })
        msg_dir = factory_dir / "messages"
        assert msg_dir.exists()
        files = list(msg_dir.iterdir())
        assert len(files) == 1
        content = files[0].read_text()
        assert "algorithm-change" in content
        assert "target_score" in content

    def test_reads_mode_and_workflow(self) -> None:
        from factory.inner_loop import FactoryCeoExecutor
        executor = FactoryCeoExecutor(mode="research", workflow=None)
        assert executor.mode == "research"
        assert executor.workflow is None

    def test_directive_count_increments(self, tmp_path: Path) -> None:
        from factory.inner_loop import FactoryCeoExecutor
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        executor = FactoryCeoExecutor(mode="evolve")
        executor._write_directives(factory_dir, {"a": 1})
        executor._write_directives(factory_dir, {"b": 2})
        msg_dir = factory_dir / "messages"
        assert len(list(msg_dir.iterdir())) == 2


class TestSkillOptExecutorIntegration:
    def test_skillopt_executor_plugs_into_inner_loop(self, tmp_path: Path) -> None:
        from factory.skillopt.types import RolloutResult
        from factory.skillopt.adapter import EnvAdapter

        MOCK_ROLLOUT_RESULTS = [
            RolloutResult(
                id="searchqa-001", hard=1.0, soft=1.0, fail_reason="",
                extras={"prediction": "Paris", "gold": "Paris"},
            ),
            RolloutResult(
                id="searchqa-002", hard=0.0, soft=0.3, fail_reason="wrong_answer",
                extras={"prediction": "London", "gold": "Berlin"},
            ),
            RolloutResult(
                id="searchqa-003", hard=1.0, soft=0.8, fail_reason="",
                extras={"prediction": "42", "gold": "42"},
            ),
            RolloutResult(
                id="searchqa-004", hard=0.0, soft=0.0, fail_reason="timeout",
                extras={"prediction": "", "gold": "Einstein"},
            ),
        ]

        class MockSearchQAAdapter(EnvAdapter):
            def build_train_env(self, batch_size: int, seed: int) -> Any:
                return {"batch_size": batch_size, "seed": seed}

            def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
                return {"env_num": env_num, "split": split, "seed": seed}

            def rollout(self, env_manager: Any, skill_content: str, out_dir: str) -> list[RolloutResult]:
                return list(MOCK_ROLLOUT_RESULTS)

            def get_task_types(self) -> list[str]:
                return ["searchqa"]

        class SkillOptExecutor:
            def __init__(self, adapter: EnvAdapter) -> None:
                self.adapter = adapter
                self._call_count = 0

            def execute(
                self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
            ) -> CycleRecord:
                env = self.adapter.build_train_env(batch_size=4, seed=self._call_count)
                skill_content = directives.get("skill_content", "") if directives else ""
                results = self.adapter.rollout(env, skill_content, str(project_dir / ".skillopt" / f"step_{self._call_count}"))
                self._call_count += 1

                hard_scores = [r.hard for r in results]
                avg_score = sum(hard_scores) / len(hard_scores) if hard_scores else 0.0

                return CycleRecord(
                    cycle_number=0,
                    mode="skillopt",
                    started_at=None,
                    ended_at=None,
                    duration_s=0.0,
                    score_start=None,
                    score_end=avg_score,
                    score_delta=None,
                    item_results=[
                        {"id": r.id, "hard": r.hard, "soft": r.soft, "fail_reason": r.fail_reason, "extras": r.extras}
                        for r in results
                    ],
                )

        adapter = MockSearchQAAdapter()
        executor = SkillOptExecutor(adapter)
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(project_dir=proj, executor=executor)

        record = loop.step(directives={"skill_content": "Solve the search question step by step."})
        assert record.cycle_number == 1
        assert len(record.item_results) == 4
        assert record.item_results[0]["id"] == "searchqa-001"
        assert record.item_results[0]["hard"] == 1.0
        assert record.item_results[1]["fail_reason"] == "wrong_answer"
        assert record.item_results[3]["extras"]["gold"] == "Einstein"
        assert record.score_end == pytest.approx(0.5)

        record2 = loop.step(directives={"skill_content": "Improved prompt..."})
        assert record2.cycle_number == 2
        assert loop.score_trajectory() == [0.5, 0.5]
        assert len(loop.history()) == 2
        assert loop.total_cost() == 0.0


class TestSkillOptTrainerDataFlow:
    def test_data_flow_from_inner_loop_to_trainer(self, tmp_path: Path) -> None:
        from factory.skillopt.types import RolloutResult
        from factory.skillopt.adapter import EnvAdapter

        MOCK_ROLLOUT_RESULTS = [
            RolloutResult(
                id="searchqa-001", hard=1.0, soft=1.0, fail_reason="",
                extras={"prediction": "Paris", "gold": "Paris"},
            ),
            RolloutResult(
                id="searchqa-002", hard=0.0, soft=0.3, fail_reason="wrong_answer",
                extras={"prediction": "London", "gold": "Berlin"},
            ),
            RolloutResult(
                id="searchqa-003", hard=1.0, soft=0.8, fail_reason="",
                extras={"prediction": "42", "gold": "42"},
            ),
            RolloutResult(
                id="searchqa-004", hard=0.0, soft=0.0, fail_reason="timeout",
                extras={"prediction": "", "gold": "Einstein"},
            ),
        ]

        class MockSearchQAAdapter(EnvAdapter):
            def build_train_env(self, batch_size: int, seed: int) -> Any:
                return {"batch_size": batch_size, "seed": seed}

            def build_eval_env(self, env_num: int, split: str, seed: int) -> Any:
                return {"env_num": env_num, "split": split, "seed": seed}

            def rollout(self, env_manager: Any, skill_content: str, out_dir: str) -> list[RolloutResult]:
                return list(MOCK_ROLLOUT_RESULTS)

            def get_task_types(self) -> list[str]:
                return ["searchqa"]

        class SkillOptExecutor:
            def __init__(self, adapter: EnvAdapter) -> None:
                self.adapter = adapter
                self._call_count = 0

            def execute(
                self, project_dir: Path, directives: dict[str, Any] | None = None, **kwargs: Any,
            ) -> CycleRecord:
                env = self.adapter.build_train_env(batch_size=4, seed=self._call_count)
                skill_content = directives.get("skill_content", "") if directives else ""
                results = self.adapter.rollout(env, skill_content, str(project_dir / ".skillopt" / f"step_{self._call_count}"))
                self._call_count += 1

                hard_scores = [r.hard for r in results]
                avg_score = sum(hard_scores) / len(hard_scores) if hard_scores else 0.0

                return CycleRecord(
                    cycle_number=0,
                    mode="skillopt",
                    started_at=None,
                    ended_at=None,
                    duration_s=0.0,
                    score_start=None,
                    score_end=avg_score,
                    score_delta=None,
                    item_results=[
                        {"id": r.id, "hard": r.hard, "soft": r.soft, "fail_reason": r.fail_reason, "extras": r.extras}
                        for r in results
                    ],
                )

        adapter = MockSearchQAAdapter()
        executor = SkillOptExecutor(adapter)
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        inner_loop = InnerLoop(project_dir=proj, executor=executor)

        current_skill = "Answer the question carefully."
        record = inner_loop.step(directives={"skill_content": current_skill})

        assert len(record.item_results) == 4
        hard = sum(item["hard"] for item in record.item_results) / len(record.item_results)
        soft = sum(item["soft"] for item in record.item_results) / len(record.item_results)
        assert hard == pytest.approx(0.5)
        assert soft == pytest.approx(0.525)

        reconstructed = [
            RolloutResult(
                id=item["id"], hard=item["hard"], soft=item["soft"],
                fail_reason=item["fail_reason"], extras=item["extras"],
            )
            for item in record.item_results
        ]
        assert len(reconstructed) == 4
        assert reconstructed[0].id == "searchqa-001"
        assert reconstructed[1].fail_reason == "wrong_answer"

        inner_loop.step(directives={"skill_content": "Improved prompt."})
        trajectory = inner_loop.score_trajectory()
        assert len(trajectory) == 2
        assert trajectory == [0.5, 0.5]


class TestExistingTestsUnmodified:
    def test_cycle_analyzer_basic_passes(self, factory_dir: Path) -> None:
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.experiments == []

    def test_cycle_analyzer_trajectory_passes(self, factory_dir: Path) -> None:
        events = _make_events(n_experiments=2, scores=[0.5, 0.8])
        _write_events(factory_dir, events)
        assert CycleAnalyzer(factory_dir).trajectory() == [0.5, 0.8]

    def test_inner_loop_unit_passes(self, tmp_path: Path) -> None:
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".factory").mkdir()
        loop = InnerLoop(proj, mode="evolve")
        assert loop.score_trajectory() == []
        assert loop.total_cost() == 0.0
        assert loop.history() == []
        r = loop.collect()
        assert r.mode == "evolve"

    def test_circle_packing_evaluator_passes(self, tmp_path: Path) -> None:
        assert isinstance(CirclePackingEvaluator(), Evaluator)
        f = tmp_path / "eval.json"
        f.write_text(json.dumps({
            "sum_radii": 2.1, "target_ratio": 0.8,
            "validity": 1.0, "eval_time": 1.5, "combined_score": 0.8,
        }))
        r = CirclePackingEvaluator().parse(f)
        assert r.score == 0.8
        assert r.valid is True
