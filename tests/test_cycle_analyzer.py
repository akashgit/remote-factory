"""Tests for CycleAnalyzer and InnerLoop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.cycle_analyzer import CycleAnalyzer
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
    (factory_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _write_results_tsv(factory_dir: Path, rows: list[dict]) -> None:
    cols = [
        "id",
        "timestamp",
        "hypothesis",
        "change_summary",
        "issue_number",
        "pr_number",
        "score_before",
        "score_after",
        "delta",
        "verdict",
        "cost_usd",
        "notes",
        "research_citations",
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
        events.append(
            {
                "type": "experiment.begin",
                "timestamp": f"2026-07-22T10:{minute:02d}:00+00:00",
                "project": "test",
                "agent": None,
                "data": {"exp_id": i + 1, "hypothesis": f"hypothesis {i + 1}"},
            }
        )
        events.append(
            {
                "type": "agent.started",
                "timestamp": f"2026-07-22T10:{minute:02d}:01+00:00",
                "project": "test",
                "agent": "builder",
                "data": {},
            }
        )
        events.append(
            {
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
            }
        )
        events.append(
            {
                "type": "eval.completed",
                "timestamp": f"2026-07-22T10:{minute + 6:02d}:00+00:00",
                "project": "test",
                "agent": None,
                "data": {"composite": scores[i], "passed": True},
            }
        )
        events.append(
            {
                "type": "experiment.finalize",
                "timestamp": f"2026-07-22T10:{minute + 7:02d}:00+00:00",
                "project": "test",
                "agent": None,
                "data": {
                    "exp_id": i + 1,
                    "verdict": verdicts[i],
                    "hypothesis": f"hypothesis {i + 1}",
                },
            }
        )
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
            json.dumps({"no_type": True, "timestamp": "2026-07-22T10:00:00Z"})
            + "\n"
            + json.dumps({"type": "test", "no_timestamp": True})
            + "\n"
            + json.dumps({"type": "detect", "timestamp": "2026-07-22T10:00:00Z", "data": {}})
            + "\n"
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
            {
                "type": "agent.started",
                "timestamp": "2026-07-22T10:00:00+00:00",
                "project": "test",
                "agent": "builder",
                "data": {},
            },
            {
                "type": "agent.failed",
                "timestamp": "2026-07-22T10:05:00+00:00",
                "project": "test",
                "agent": "builder",
                "data": {"return_code": 1, "stderr": "timed out"},
            },
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
        _write_results_tsv(
            factory_dir,
            [
                {
                    "id": "1",
                    "hypothesis": "better hypothesis",
                    "score_before": "0.3",
                    "score_after": "0.5",
                    "verdict": "keep",
                },
            ],
        )
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert r.experiments[0].score_before == 0.3
        assert r.experiments[0].score_after == 0.5
        assert r.experiments[0].score_delta == pytest.approx(0.2)

    def test_adds_missing_experiments(self, factory_dir: Path) -> None:
        _write_results_tsv(
            factory_dir,
            [
                {
                    "id": "1",
                    "hypothesis": "h1",
                    "score_before": "0.3",
                    "score_after": "0.5",
                    "verdict": "keep",
                },
                {
                    "id": "2",
                    "hypothesis": "h2",
                    "score_before": "0.5",
                    "score_after": "0.4",
                    "verdict": "revert",
                },
            ],
        )
        r = CycleAnalyzer(factory_dir).latest()
        assert r is not None
        assert len(r.experiments) == 2
        assert r.kept == 1
        assert r.reverted == 1

    def test_tsv_scores_override_events(self, factory_dir: Path) -> None:
        _write_results_tsv(
            factory_dir,
            [
                {"id": "1", "score_after": "0.5", "verdict": "keep"},
                {"id": "2", "score_after": "0.8", "verdict": "keep"},
                {"id": "3", "score_after": "1.0", "verdict": "keep"},
            ],
        )
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
        _write_results_tsv(
            factory_dir,
            [
                {"id": "1", "verdict": "keep"},
            ],
        )
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
            {
                "type": "agent.started",
                "timestamp": "2026-07-22T10:00:00+00:00",
                "project": "test",
                "agent": "researcher",
                "data": {},
            },
            {
                "type": "agent.completed",
                "timestamp": "2026-07-22T10:05:00+00:00",
                "project": "test",
                "agent": "researcher",
                "data": {"return_code": 0, "total_cost_usd": 1.0},
            },
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
            {
                "type": "agent.started",
                "timestamp": "2026-07-22T10:00:00+00:00",
                "project": "test",
                "agent": "builder",
                "data": {},
            },
            {
                "type": "agent.completed",
                "timestamp": "2026-07-22T10:05:00+00:00",
                "project": "test",
                "agent": "builder",
                "data": {"return_code": 0, "total_cost_usd": 2.0},
            },
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
        f.write_text(
            json.dumps(
                {
                    "sum_radii": 2.1,
                    "target_ratio": 0.8,
                    "validity": 1.0,
                    "eval_time": 1.5,
                    "combined_score": 0.8,
                }
            )
        )
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
        _write_results_tsv(
            fd,
            [
                {
                    "id": "1",
                    "hypothesis": "h1",
                    "score_before": "0.3",
                    "score_after": "0.5",
                    "verdict": "keep",
                },
            ],
        )
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
        (exp_dir / "eval_after.json").write_text(
            json.dumps(
                {
                    "combined_score": 0.85,
                    "validity": 1.0,
                    "sum_radii": 2.1,
                }
            )
        )

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
        loop._write_directives(
            {
                "prefer_categories": ["algorithm-change"],
                "target_score": 1.0,
            }
        )
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
