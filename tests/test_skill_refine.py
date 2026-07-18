"""Tests for the LLM refinement pipeline in export_all_skills()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from factory.workflow.primitives import AgentNode, AgentRole, Edge, FnNode, Workflow
from factory.workflow.skill_export import (
    _count_changed_slots,
    export_all_skills,
    workflow_to_skill_md,
)
from factory.workflow.splitter import _SLOT_PREFIXES, _slot_belongs_to_node
from factory.workflow.templates import extract


# ── helpers ──────────────────────────────────────────────────────


def _make_agent(
    id: str,
    role: AgentRole = AgentRole.BUILDER,
    *,
    prompt: str = "Do the build.",
) -> AgentNode:
    return AgentNode(
        id=id,
        role=role,
        blocking=True,
        prompt_template=prompt,
        reads=set(),
        writes=set(),
    )


def _minimal_workflow(name: str = "test_wf") -> Workflow:
    builder = _make_agent("builder")
    return Workflow(
        name=name,
        nodes={"builder": builder},
        edges=[],
        start_node="builder",
    )


def _workflow_with_fn_notes() -> Workflow:
    fn = FnNode(
        id="fn_begin",
        command="factory begin {project_path}",
        notes="Open a new experiment for the current hypothesis.",
    )
    builder = _make_agent("builder")
    return Workflow(
        name="test_notes",
        nodes={"fn_begin": fn, "builder": builder},
        edges=[Edge(source="fn_begin", target="builder")],
        start_node="fn_begin",
    )


# ── FnNode notes slots ──────────────────────────────────────────


class TestFnNodeNotesSlots:
    def test_notes_emit_template_slot(self) -> None:
        wf = _workflow_with_fn_notes()
        result = workflow_to_skill_md(wf)
        assert "{{notes_fn_begin::" in result
        assert "Open a new experiment" in result

    def test_notes_slot_before_bash_block(self) -> None:
        wf = _workflow_with_fn_notes()
        result = workflow_to_skill_md(wf)
        idx_slot = result.index("{{notes_fn_begin::")
        idx_bash = result.index("```bash", idx_slot)
        assert idx_slot < idx_bash

    def test_empty_notes_no_slot(self) -> None:
        fn = FnNode(id="fn_eval", command="factory eval {project_path}")
        wf = Workflow(
            name="test_empty",
            nodes={"fn_eval": fn},
            edges=[],
            start_node="fn_eval",
        )
        result = workflow_to_skill_md(wf)
        assert "{{notes_" not in result

    def test_notes_extractable_as_slot(self) -> None:
        wf = _workflow_with_fn_notes()
        result = workflow_to_skill_md(wf)
        slots = dict(extract(result))
        assert "notes_fn_begin" in slots
        assert slots["notes_fn_begin"] == "Open a new experiment for the current hypothesis."


# ── _SLOT_PREFIXES ───────────────────────────────────────────────


class TestSlotPrefixes:
    def test_notes_in_prefixes(self) -> None:
        assert "notes_" in _SLOT_PREFIXES

    def test_slot_belongs_to_node_notes(self) -> None:
        assert _slot_belongs_to_node("notes_fn_begin", "fn_begin")

    def test_slot_belongs_to_node_notes_negative(self) -> None:
        assert not _slot_belongs_to_node("notes_fn_begin", "fn_eval")


# ── _count_changed_slots ─────────────────────────────────────────


class TestCountChangedSlots:
    def test_no_changes(self) -> None:
        skeleton = "text {{a::1}} more {{b::2}}"
        assert _count_changed_slots(skeleton, skeleton) == 0

    def test_one_change(self) -> None:
        skeleton = "text {{a::1}} more {{b::2}}"
        candidate = "text {{a::changed}} more {{b::2}}"
        assert _count_changed_slots(skeleton, candidate) == 1

    def test_all_changed(self) -> None:
        skeleton = "text {{a::1}} more {{b::2}}"
        candidate = "text {{a::x}} more {{b::y}}"
        assert _count_changed_slots(skeleton, candidate) == 2


# ── export_all_skills refine=False ────────────────────────────────


class TestExportNoRefine:
    async def test_no_refine_produces_output(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        paths = await export_all_skills(
            tmp_path, {"test_wf": wf}, refine=False,
        )
        assert len(paths) == 1
        assert paths[0].exists()
        content = paths[0].read_text()
        assert "workflow-test_wf" in content

    async def test_no_refine_no_agent_calls(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        with patch(
            "factory.agents.runner.invoke_agents_parallel"
        ) as mock_par, patch(
            "factory.agents.runner.invoke_agent"
        ) as mock_single:
            await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=False,
            )
            mock_par.assert_not_called()
            mock_single.assert_not_called()


# ── export_all_skills refine=True ─────────────────────────────────


class TestExportWithRefine:
    async def test_spawns_3_reviewers(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        templatized = workflow_to_skill_md(wf)

        mock_parallel = AsyncMock(return_value=[
            (templatized, 0),
            (templatized, 0),
            (templatized, 0),
        ])
        mock_synth = AsyncMock(return_value=(templatized, 0))

        with patch(
            "factory.agents.runner.invoke_agents_parallel",
            mock_parallel,
        ), patch(
            "factory.agents.runner.invoke_agent",
            mock_synth,
        ):
            await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=True,
            )

            mock_parallel.assert_called_once()
            call_args = mock_parallel.call_args
            tasks = call_args[0][0]
            assert len(tasks) == 3
            assert all(role == "skill_reviewer" for role, _ in tasks)

    async def test_synthesizer_receives_survivors(self, tmp_path: Path) -> None:
        wf = _minimal_workflow()
        templatized = workflow_to_skill_md(wf)

        mock_parallel = AsyncMock(return_value=[
            (templatized, 0),
            (templatized, 0),
            ("bad output", 1),
        ])
        mock_synth = AsyncMock(return_value=(templatized, 0))

        with patch(
            "factory.agents.runner.invoke_agents_parallel",
            mock_parallel,
        ), patch(
            "factory.agents.runner.invoke_agent",
            mock_synth,
        ):
            await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=True,
            )

            mock_synth.assert_called_once()
            call_args = mock_synth.call_args
            assert call_args[0][0] == "skill_synthesizer"
            synth_task = call_args[0][1]
            assert "## Original" in synth_task
            assert "## Candidate 1" in synth_task
            assert "## Candidate 2" in synth_task


# ── fallback chain ────────────────────────────────────────────────


class TestFallbackChain:
    async def test_all_candidates_fail_guard_mechanical_fallback(
        self, tmp_path: Path,
    ) -> None:
        wf = _minimal_workflow()
        templatized = workflow_to_skill_md(wf)

        bad_output = templatized + "\n<!-- injected annotation -->"

        mock_parallel = AsyncMock(return_value=[
            (bad_output, 0),
            (bad_output, 0),
            (bad_output, 0),
        ])

        with patch(
            "factory.agents.runner.invoke_agents_parallel",
            mock_parallel,
        ), patch(
            "factory.agents.runner.invoke_agent",
        ) as mock_synth:
            paths = await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=True,
            )
            mock_synth.assert_not_called()
            content = paths[0].read_text()
            assert "workflow-test_wf" in content

    async def test_synthesizer_fails_guard_best_individual(
        self, tmp_path: Path,
    ) -> None:
        wf = _minimal_workflow()
        templatized = workflow_to_skill_md(wf)

        mock_parallel = AsyncMock(return_value=[
            (templatized, 0),
            (templatized, 0),
            (templatized, 0),
        ])
        bad_synth = templatized + "\n<!-- injected -->"
        mock_synth = AsyncMock(return_value=(bad_synth, 0))

        with patch(
            "factory.agents.runner.invoke_agents_parallel",
            mock_parallel,
        ), patch(
            "factory.agents.runner.invoke_agent",
            mock_synth,
        ):
            paths = await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=True,
            )
            assert paths[0].exists()

    async def test_all_reviewers_return_nonzero_mechanical_fallback(
        self, tmp_path: Path,
    ) -> None:
        wf = _minimal_workflow()

        mock_parallel = AsyncMock(return_value=[
            ("", 1),
            ("", 1),
            ("", 1),
        ])

        with patch(
            "factory.agents.runner.invoke_agents_parallel",
            mock_parallel,
        ), patch(
            "factory.agents.runner.invoke_agent",
        ) as mock_synth:
            paths = await export_all_skills(
                tmp_path, {"test_wf": wf}, refine=True,
            )
            mock_synth.assert_not_called()
            assert paths[0].exists()


# ── synthesizer prompt exists ──────────────────────────────────────


class TestSynthesizerPrompt:
    def test_prompt_file_exists(self) -> None:
        prompt_path = (
            Path(__file__).resolve().parent.parent
            / "factory" / "agents" / "prompts" / "skill_synthesizer.md"
        )
        assert prompt_path.exists(), f"Missing prompt: {prompt_path}"

    def test_prompt_loadable(self) -> None:
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt("skill_synthesizer")
        assert "synthesizer" in prompt.lower()
        assert "slot" in prompt.lower()
