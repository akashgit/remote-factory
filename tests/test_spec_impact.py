"""Tests for factory.spec.impact — agent-based module impact analysis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from factory.spec.impact import get_impact

SAMPLE_SPEC = """\
# GRAPH-SPEC

## Modules

### models
- **Path:** `factory/models.py`
- **Role:** All Pydantic v2 strict models
- **Classification:** hub
- **Depends on:** none

### store
- **Path:** `factory/store.py`
- **Role:** Experiment store
- **Classification:** hub
- **Depends on:** models

### cli
- **Path:** `factory/cli.py`
- **Role:** CLI entry point
- **Classification:** leaf
- **Depends on:** store, models
"""

IMPACT_MARKDOWN = """\
## Impact: models

**Path:** `factory/models.py`
**Role:** All Pydantic v2 strict models
**Classification:** hub

### Dependencies (imports)
- None

### Dependents (imported by)
- store
- cli

### Contracts Owned
- ProjectState (used by: cli, store, risk: high)
- FactoryConfig (used by: cli, store, risk: medium)

### Change Impact
- severity: high, affects: cli, store, spec
"""


def _mock_impact_agent() -> AsyncMock:
    """Return a mock invoke_agent that returns impact markdown."""
    return AsyncMock(return_value=(IMPACT_MARKDOWN, 0))


def _mock_impact_agent_failure() -> AsyncMock:
    return AsyncMock(return_value=("error", 1))


@pytest.fixture
def project_with_spec(tmp_path: Path) -> Path:
    (tmp_path / "GRAPH-SPEC.md").write_text(SAMPLE_SPEC)
    return tmp_path


class TestGetImpact:
    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_returns_markdown(self, mock_agent: AsyncMock, project_with_spec: Path) -> None:
        result = await get_impact("models", project_with_spec)
        assert "## Impact: models" in result
        assert "**Classification:** hub" in result

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_includes_dependents(
        self, mock_agent: AsyncMock, project_with_spec: Path
    ) -> None:
        result = await get_impact("models", project_with_spec)
        assert "store" in result
        assert "cli" in result

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_includes_contracts(self, mock_agent: AsyncMock, project_with_spec: Path) -> None:
        result = await get_impact("models", project_with_spec)
        assert "### Contracts Owned" in result
        assert "ProjectState" in result

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_includes_change_impact(
        self, mock_agent: AsyncMock, project_with_spec: Path
    ) -> None:
        result = await get_impact("models", project_with_spec)
        assert "### Change Impact" in result
        assert "high" in result

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_haiku_model_used(self, mock_agent: AsyncMock, project_with_spec: Path) -> None:
        await get_impact("models", project_with_spec)
        assert mock_agent.call_args.kwargs.get("model") == "haiku"

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent_failure)
    async def test_agent_failure_raises(
        self, mock_agent: AsyncMock, project_with_spec: Path
    ) -> None:
        with pytest.raises(RuntimeError, match="Impact analysis agent failed"):
            await get_impact("models", project_with_spec)

    async def test_missing_spec_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await get_impact("models", tmp_path)

    @patch("factory.spec.impact.invoke_agent", new_callable=_mock_impact_agent)
    async def test_compact_output(self, mock_agent: AsyncMock, project_with_spec: Path) -> None:
        result = await get_impact("models", project_with_spec)
        lines = result.strip().splitlines()
        assert len(lines) < 30


class TestWorkflowSpecAwareness:
    """Verify that workflow nodes reference GRAPH-SPEC.md in prompt_template."""

    def test_build_researchers_mention_spec(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        for nid in ("researcher_similar", "researcher_techstack", "researcher_pitfalls"):
            node = wf.nodes[nid]
            assert "GRAPH-SPEC.md" in node.prompt_template

    def test_improve_researcher_mentions_spec(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        assert "GRAPH-SPEC.md" in wf.nodes["researcher"].prompt_template

    def test_build_strategist_mentions_spec(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        assert "GRAPH-SPEC Diff" in wf.nodes["strategist"].prompt_template

    def test_improve_strategist_mentions_spec(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        assert "GRAPH-SPEC Diff" in wf.nodes["strategist"].prompt_template

    def test_build_has_spec_gate(self) -> None:
        from factory.workflow.definitions import build_workflow

        wf = build_workflow()
        assert "gate_spec_exists" in wf.nodes
        assert "generate_spec" in wf.nodes
        assert "gate_spec_updated" in wf.nodes
        assert wf.start_node == "gate_spec_exists"

    def test_improve_has_spec_gate(self) -> None:
        from factory.workflow.definitions import improve_workflow

        wf = improve_workflow()
        assert "gate_spec_exists" in wf.nodes
        assert "generate_spec" in wf.nodes
        assert "gate_spec_updated" in wf.nodes
        assert wf.start_node == "gate_spec_exists"
