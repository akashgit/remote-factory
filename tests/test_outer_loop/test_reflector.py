"""Tests for OuterLoopReflector contrastive reflection."""

from __future__ import annotations

from pathlib import Path

from factory.cycle_analyzer import AgentStep, CycleRecord
from factory.outer_loop.reflector import OuterLoopReflector, ReflectionReport


def _make_record(
    score: float,
    steps: list[AgentStep] | None = None,
    kept: int = 0,
    reverted: int = 0,
    errored: int = 0,
    eval_details: dict | None = None,
    instance_results: list[dict] | None = None,
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
        eval_details=eval_details,
        instance_results=instance_results,
    )


def _make_step(role: str, succeeded: bool = True, error: str | None = None, duration: float = 10.0) -> AgentStep:
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


class TestOuterLoopReflector:
    def test_basic_reflection(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("winner1", 0.9, _make_record(0.9, [_make_step("builder"), _make_step("researcher")], kept=2)),
            ("loser1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False, error="timeout")], errored=1)),
        ]

        report = reflector.reflect(records, generation=0)

        assert len(report.failure_patterns) > 0
        assert len(report.success_patterns) > 0
        assert report.top_k_ids == ["winner1"]
        assert report.bottom_k_ids == ["loser1"]

    def test_mutation_suggestions_from_role_diff(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("researcher"), _make_step("builder")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [_make_step("builder")], reverted=1)),
        ]

        report = reflector.reflect(records, generation=0)

        role_suggestions = [s for s in report.mutation_suggestions if "researcher" in s.lower()]
        assert len(role_suggestions) > 0

    def test_insufficient_data(self) -> None:
        reflector = OuterLoopReflector(k=1)
        records = [("only1", 0.5, _make_record(0.5))]
        report = reflector.reflect(records, generation=0)

        assert len(report.failure_patterns) == 0
        assert len(report.success_patterns) == 0

    def test_none_records_filtered(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("builder")], kept=1)),
            ("n1", 0.5, None),
            ("l1", 0.2, _make_record(0.2, [_make_step("builder", succeeded=False)], errored=1)),
        ]

        report = reflector.reflect(records, generation=0)
        assert len(report.top_k_ids) == 1
        assert len(report.bottom_k_ids) == 1

    def test_save_report(self, tmp_path: Path) -> None:
        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)

        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("builder")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [], errored=1)),
        ]

        reflector.reflect(records, generation=3)

        json_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen3.json"
        md_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen3.md"
        assert json_path.exists()
        assert md_path.exists()

    def test_structural_recommendations_timeout(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=1)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False, duration=600.0)])),
        ]

        report = reflector.reflect(records, generation=0)
        timeout_recs = [r for r in report.structural_recommendations if "timeout" in r.lower()]
        assert len(timeout_recs) > 0

    def test_multiple_winners_losers(self) -> None:
        reflector = OuterLoopReflector(k=2)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("w2", 0.85, _make_record(0.85, [_make_step("builder"), _make_step("researcher")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [], errored=1)),
            ("l2", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], reverted=2)),
        ]

        report = reflector.reflect(records, generation=0)
        assert len(report.top_k_ids) == 2
        assert len(report.bottom_k_ids) == 2

    def test_reflect_with_knob_values_produces_suggestions(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], errored=1)),
        ]

        kvbi = {
            "w1": {"style": "focused", "_prompt_builder": "Be precise"},
            "l1": {"style": "broad", "_prompt_builder": "Be creative"},
        }

        report = reflector.reflect(records, generation=0, knob_values_by_id=kvbi)

        knob_suggestions = [
            s for s in report.mutation_suggestions if "KNOB_MUTATE" in s or "PROMPT_MUTATE" in s
        ]
        assert len(knob_suggestions) > 0

        assert len(report.prompt_improvements) > 0

    def test_llm_reflect_non_dict_json_gracefully_returns(self) -> None:
        """_llm_reflect should not crash when LLM returns non-dict JSON."""
        from unittest.mock import patch, MagicMock

        reflector = OuterLoopReflector(k=1, llm_reflect=True)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], errored=1)),
        ]

        non_dict_payloads = [
            '[1, 2, 3]',       # JSON array
            '42',              # JSON number
            '"hello"',         # JSON string
            'null',            # JSON null
            'true',            # JSON boolean
        ]

        for payload in non_dict_payloads:
            mock_proc = MagicMock()
            mock_proc.stdout = payload
            mock_proc.returncode = 0

            with patch("subprocess.run", return_value=mock_proc):
                report = reflector.reflect(records, generation=0)
                assert isinstance(report, ReflectionReport)

    def test_reflect_without_knob_values_no_knob_suggestions(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], errored=1)),
        ]

        report = reflector.reflect(records, generation=0)

        knob_suggestions = [
            s for s in report.mutation_suggestions if "KNOB_MUTATE" in s
        ]
        assert len(knob_suggestions) == 0


class TestCollectIndividualDetails:
    """Cover branches in _collect_individual_details."""

    def test_none_record(self) -> None:
        result = OuterLoopReflector._collect_individual_details("abc12345", 0.5, None)
        assert "abc12345" in result
        assert "0.500" in result

    def test_eval_details_with_verify_and_instances(self) -> None:
        rec = _make_record(
            0.4,
            eval_details={
                "verify": {
                    "verify_count": 3,
                    "passed_count": 1,
                    "instance_results": [
                        {"index": 0, "passed": False, "score": 0.0},
                        {"index": 1, "passed": True, "score": 1.0},
                    ],
                },
                "extra_key": "extra_value",
            },
        )
        result = OuterLoopReflector._collect_individual_details("def12345", 0.4, rec)
        assert "instance" in result
        assert "verify_count" in result
        assert "extra_key" in result

    def test_instance_results_on_record(self) -> None:
        rec = CycleRecord(
            cycle_number=1,
            mode="test",
            started_at=None,
            ended_at=None,
            duration_s=10.0,
            score_start=0.0,
            score_end=0.6,
            score_delta=0.6,
            steps=[],
            instance_results=[
                {"task_id": "t1", "passed": True},
                {"task_id": "t2", "passed": False},
            ],
        )
        result = OuterLoopReflector._collect_individual_details("ghi12345", 0.6, rec)
        assert "task_id" in result

    def test_steps_in_details(self) -> None:
        rec = _make_record(
            0.7,
            steps=[_make_step("builder"), _make_step("researcher", succeeded=False)],
        )
        result = OuterLoopReflector._collect_individual_details("jkl12345", 0.7, rec)
        assert "builder(ok)" in result
        assert "researcher(FAIL)" in result

    def test_non_dict_instance_skipped(self) -> None:
        """Non-dict items in instance_results are skipped."""
        rec = _make_record(
            0.3,
            eval_details={
                "verify": {
                    "instance_results": [
                        "not a dict",
                        42,
                        {"index": 0, "passed": True},
                    ],
                },
            },
        )
        result = OuterLoopReflector._collect_individual_details("skip123", 0.3, rec)
        assert "index" in result

    def test_verify_not_a_dict(self) -> None:
        """When verify is present but not a dict, skip verify block."""
        rec = _make_record(
            0.3,
            eval_details={
                "verify": "not a dict",
                "other_key": "some_value",
            },
        )
        result = OuterLoopReflector._collect_individual_details("verstr12", 0.3, rec)
        assert "other_key" in result

    def test_eval_details_without_verify(self) -> None:
        """eval_details without a verify key still processes other keys."""
        rec = _make_record(
            0.5,
            eval_details={"custom": "data", "score": 42},
        )
        result = OuterLoopReflector._collect_individual_details("noverify", 0.5, rec)
        assert "custom" in result
        assert "score" in result


class TestMutationSuggestionsAvgSteps:
    """Cover typed_suggestions from avg step count comparison."""

    def test_top_more_steps_produces_node_insert(self) -> None:
        """When top-K has significantly more steps than bottom-K, suggest NODE_INSERT."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(
                0.9,
                [_make_step("researcher"), _make_step("strategist"), _make_step("builder")],
                kept=2,
            )),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("builder", succeeded=False)],
                errored=1,
            )),
        ]
        report = reflector.reflect(records, generation=0)

        insert_typed = [s for s in report.typed_suggestions if s.operator == "node_insert" and s.target == "any"]
        assert len(insert_typed) > 0

    def test_bottom_more_steps_produces_node_remove(self) -> None:
        """When bottom-K has significantly more steps than top-K, suggest NODE_REMOVE."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(
                0.9,
                [_make_step("builder")],
                kept=2,
            )),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("researcher"), _make_step("strategist"), _make_step("builder", succeeded=False)],
                errored=1,
            )),
        ]
        report = reflector.reflect(records, generation=0)

        remove_typed = [s for s in report.typed_suggestions if s.operator == "node_remove" and s.target == "any"]
        assert len(remove_typed) > 0

    def test_roles_in_bottom_not_top_produces_node_remove(self) -> None:
        """When bottom has succeeded roles not in top, produce NODE_REMOVE typed suggestions."""
        reflector = OuterLoopReflector(k=1)
        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("researcher", succeeded=True), _make_step("builder", succeeded=True)],
                reverted=2,
            )),
        ]
        report = reflector.reflect(records, generation=0)

        remove_suggestions = [s for s in report.typed_suggestions if s.operator == "node_remove" and s.target == "researcher"]
        assert len(remove_suggestions) > 0


class TestLLMReflectPayloadTruncation:
    """Cover the payload truncation branch in _llm_reflect."""

    def test_long_payload_truncated(self) -> None:
        """Payload exceeding _LLM_PAYLOAD_BUDGET (8000) gets truncated."""
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=3)
        long_details = {f"key_{i}": "x" * 600 for i in range(10)}
        recs_top = [
            (f"w{i}", 0.9, _make_record(0.9, [_make_step("builder")], eval_details=long_details))
            for i in range(3)
        ]
        recs_bottom = [
            (f"l{i}", 0.1, _make_record(0.1, [_make_step("builder")], eval_details=long_details))
            for i in range(3)
        ]
        report = ReflectionReport()

        captured_args: list = []

        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            captured_args.append(args)
            mock = MagicMock()
            mock.stdout = '{"prompt_improvements": ["Be concise"], "failure_patterns": []}'
            mock.returncode = 0
            return mock

        with patch("factory.outer_loop.reflector.subprocess.run", side_effect=fake_run):
            reflector._llm_reflect(recs_top, recs_bottom, [], report)

        assert "Be concise" in report.prompt_improvements

    def test_llm_reflect_with_failure_and_improvement_items(self) -> None:
        """Cover the full extraction path: both improvements and failures populated."""
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        fake_response = (
            '{"prompt_improvements": ["Check errors first", "Use step-by-step"], '
            '"failure_patterns": ["Skipped tests", "Wrong file"]}'
        )

        with patch("factory.outer_loop.reflector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_response
            mock_run.return_value.returncode = 0
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert len(report.prompt_improvements) == 2
        assert len(report.failure_patterns) == 2

    def test_llm_reflect_non_string_items_skipped(self) -> None:
        """Non-string items in improvements/failures lists should be skipped."""
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        fake_response = (
            '{"prompt_improvements": ["Valid", 42, null, ""], '
            '"failure_patterns": [123, "Real failure"]}'
        )

        with patch("factory.outer_loop.reflector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_response
            mock_run.return_value.returncode = 0
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert report.prompt_improvements == ["Valid"]
        assert report.failure_patterns == ["Real failure"]

    def test_llm_reflect_non_list_improvements_ignored(self) -> None:
        """Non-list prompt_improvements/failure_patterns handled gracefully."""
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        fake_response = '{"prompt_improvements": "not a list", "failure_patterns": 42}'

        with patch("factory.outer_loop.reflector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_response
            mock_run.return_value.returncode = 0
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert report.prompt_improvements == []
        assert report.failure_patterns == []

    def test_llm_reflect_value_error_handled(self) -> None:
        """ValueError from subprocess should be caught."""
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        with patch("factory.outer_loop.reflector.subprocess.run", side_effect=ValueError("bad")):
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert report.prompt_improvements == []


class TestStructuralRecommendationsForkNode:
    """Cover the ForkNode parallel execution recommendation path."""

    def test_fork_node_in_top_k_produces_parallelize_suggestion(self) -> None:
        from factory.cycle_analyzer import NodeTrace

        reflector = OuterLoopReflector(k=1)
        rec_with_fork = CycleRecord(
            cycle_number=1,
            mode="test",
            started_at=None,
            ended_at=None,
            duration_s=10.0,
            score_start=0.0,
            score_end=0.9,
            score_delta=0.9,
            steps=[_make_step("builder")],
            kept=2,
            node_trace={
                "fork_1": NodeTrace(
                    node_id="fork_1",
                    node_type="ForkNode",
                    role=None,
                    declared_writes=set(),
                    declared_reads=set(),
                ),
                "builder": NodeTrace(
                    node_id="builder",
                    node_type="AgentNode",
                    role="builder",
                    declared_writes=set(),
                    declared_reads=set(),
                ),
            },
        )
        records = [
            ("w1", 0.9, rec_with_fork),
            ("l1", 0.1, _make_record(0.1, [_make_step("builder", succeeded=False)], errored=1)),
        ]
        report = reflector.reflect(records, generation=0)

        parallelize_typed = [s for s in report.typed_suggestions if s.operator == "parallelize"]
        assert len(parallelize_typed) > 0
        parallelize_recs = [r for r in report.structural_recommendations if "PARALLELIZE" in r]
        assert len(parallelize_recs) > 0


class TestSaveReportAllSections:
    """Cover all sections of _save_report markdown rendering."""

    def test_save_report_typed_suggestion_without_value(self, tmp_path: Path) -> None:
        from factory.outer_loop.reflector import MutationSuggestion

        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)
        report = ReflectionReport(
            typed_suggestions=[
                MutationSuggestion(
                    operator="node_insert",
                    target="researcher",
                    rationale="Add researcher for coverage",
                    value=None,
                ),
            ],
            top_k_ids=["w1"],
            bottom_k_ids=["l1"],
        )
        reflector._save_report(report, generation=11)

        md_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen11.md"
        md_content = md_path.read_text()
        assert "## Typed Suggestions" in md_content
        assert "[node_insert] researcher:" in md_content
        assert "value=" not in md_content

    def test_save_report_empty_report(self, tmp_path: Path) -> None:
        import json

        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)
        report = ReflectionReport(top_k_ids=["w1"], bottom_k_ids=["l1"])
        reflector._save_report(report, generation=12)

        json_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen12.json"
        data = json.loads(json_path.read_text())
        assert data["typed_suggestions"] == []
        assert data["prompt_improvements"] == []


class TestLLMReflectJSONExtraction:
    """Cover JSON extraction branches in _llm_reflect."""

    def test_json_embedded_in_text(self) -> None:
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9, [_make_step("builder")]))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        response_with_prefix = (
            'Here is my analysis:\n'
            '{"prompt_improvements": ["Try harder"], "failure_patterns": ["Bad strategy"]}\n'
            'Hope this helps!'
        )

        with patch("factory.outer_loop.reflector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = response_with_prefix
            mock_run.return_value.returncode = 0
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert "Try harder" in report.prompt_improvements
        assert "Bad strategy" in report.failure_patterns

    def test_non_dict_json_returns_early(self) -> None:
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        with patch("factory.outer_loop.reflector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "[1, 2, 3]"
            mock_run.return_value.returncode = 0
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert report.prompt_improvements == []

    def test_os_error_handled(self) -> None:
        from unittest.mock import patch

        reflector = OuterLoopReflector(k=1)
        top_k = [("w1", 0.9, _make_record(0.9))]
        bottom_k = [("l1", 0.1, _make_record(0.1))]
        report = ReflectionReport()

        with patch("factory.outer_loop.reflector.subprocess.run", side_effect=OSError("no such file")):
            reflector._llm_reflect(top_k, bottom_k, [], report)

        assert report.prompt_improvements == []


class TestSaveReportTypedSuggestions:
    """Cover typed_suggestions serialization and markdown rendering in _save_report."""

    def test_save_report_with_typed_suggestions(self, tmp_path: Path) -> None:
        import json

        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)
        records = [
            ("w1", 0.8, _make_record(0.8, [_make_step("researcher"), _make_step("builder")], kept=1)),
            ("l1", 0.2, _make_record(0.2, [_make_step("builder", succeeded=False, duration=600.0)])),
        ]
        report = reflector.reflect(records, generation=7)

        json_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen7.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "typed_suggestions" in data
        assert isinstance(data["typed_suggestions"], list)
        for ts in data["typed_suggestions"]:
            assert "operator" in ts
            assert "target" in ts
            assert "rationale" in ts

        md_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen7.md"
        assert md_path.exists()
        md_content = md_path.read_text()
        if report.typed_suggestions:
            assert "## Typed Suggestions" in md_content

    def test_save_report_with_prompt_improvements(self, tmp_path: Path) -> None:
        from factory.outer_loop.reflector import MutationSuggestion

        reflector = OuterLoopReflector(k=1, project_dir=tmp_path)

        report = ReflectionReport(
            failure_patterns=["f1"],
            success_patterns=["s1"],
            mutation_suggestions=["m1"],
            prompt_improvements=["Focus on error messages"],
            structural_recommendations=["r1"],
            top_k_ids=["w1"],
            bottom_k_ids=["l1"],
            typed_suggestions=[
                MutationSuggestion(
                    operator="knob_mutate",
                    target="style",
                    rationale="Focused works best",
                    value="focused",
                ),
            ],
        )
        reflector._save_report(report, generation=9)

        md_path = tmp_path / ".factory" / "outer_loop" / "reflections" / "gen9.md"
        md_content = md_path.read_text()
        assert "## Prompt Improvements" in md_content
        assert "Focus on error messages" in md_content
        assert "## Typed Suggestions" in md_content
        assert "knob_mutate" in md_content
        assert "value=focused" in md_content


class TestExtractEvalPatternsVerify:
    """Cover verify/instance_results branches in _extract_eval_patterns."""

    def test_verify_failure_patterns_with_instance_details(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(
                0.9,
                [_make_step("builder")],
                kept=2,
                eval_details={
                    "verify": {
                        "verify_count": 3,
                        "passed_count": 3,
                    },
                },
            )),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("builder", succeeded=False)],
                eval_details={
                    "verify": {
                        "verify_count": 3,
                        "failed_count": 2,
                        "instance_results": [
                            {"index": 0, "passed": False, "details": {"returncode": 1}},
                            {"index": 1, "passed": False, "details": {"returncode": 2}},
                            {"index": 2, "passed": True, "details": {"returncode": 0}},
                        ],
                    },
                },
            )),
        ]
        report = reflector.reflect(records, generation=0)

        returncode_patterns = [p for p in report.failure_patterns if "returncode=" in p]
        assert len(returncode_patterns) > 0

    def test_verify_score_comparison_mutation_suggestion(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(
                0.9,
                [_make_step("builder")],
                kept=2,
                eval_details={
                    "verify": {
                        "verify_count": 2,
                        "passed_count": 2,
                        "instance_results": [
                            {"index": 0, "passed": True, "score": 0.9},
                            {"index": 1, "passed": True, "score": 0.95},
                        ],
                    },
                },
            )),
            ("l1", 0.1, _make_record(
                0.1,
                [_make_step("builder", succeeded=False)],
                eval_details={
                    "verify": {
                        "verify_count": 2,
                        "failed_count": 2,
                        "instance_results": [
                            {"index": 0, "passed": False, "score": 0.1},
                            {"index": 1, "passed": False, "score": 0.2},
                        ],
                    },
                },
            )),
        ]
        report = reflector.reflect(records, generation=0)

        verify_suggestions = [s for s in report.mutation_suggestions if "verify" in s.lower()]
        assert len(verify_suggestions) > 0
        typed_prompt = [s for s in report.typed_suggestions if s.operator == "prompt_mutate"]
        assert len(typed_prompt) > 0

    def test_test_details_failure_pattern(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(
                0.9, [_make_step("builder")], kept=2,
                eval_details={"test_details": {"returncode": 0, "total": 10}},
            )),
            ("l1", 0.1, _make_record(
                0.1, [_make_step("builder", succeeded=False)],
                eval_details={"test_details": {"returncode": 1, "failed": 3, "total": 10}},
            )),
        ]
        report = reflector.reflect(records, generation=0)

        test_patterns = [p for p in report.failure_patterns if "test" in p.lower()]
        assert len(test_patterns) > 0

    def test_rejected_and_error_patterns(self) -> None:
        reflector = OuterLoopReflector(k=1)

        records = [
            ("w1", 0.9, _make_record(0.9, [_make_step("builder")], kept=2)),
            ("l1", 0.1, _make_record(
                0.1, [_make_step("builder", succeeded=False)],
                eval_details={
                    "rejected": "timeout exceeded",
                    "error": "process crashed with SIGSEGV",
                },
            )),
        ]
        report = reflector.reflect(records, generation=0)

        rejected_patterns = [p for p in report.failure_patterns if "rejected" in p.lower()]
        assert len(rejected_patterns) > 0
        error_patterns = [p for p in report.failure_patterns if "error" in p.lower()]
        assert len(error_patterns) > 0
