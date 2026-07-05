"""Tests for _chain_modes terminal workflow behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.models import ProjectState
from factory.workflow.primitives import FnNode, Workflow


def _terminal_workflow() -> Workflow:
    return Workflow(
        name="swebench",
        nodes={"start": FnNode(id="start", command="true")},
        edges=[],
        start_node="start",
        terminal=True,
    )


def _non_terminal_workflow() -> Workflow:
    return Workflow(
        name="improve",
        nodes={"start": FnNode(id="start", command="true")},
        edges=[],
        start_node="start",
        terminal=False,
    )


class TestChainModesTerminal:
    def test_returns_zero_for_terminal_mode(self, tmp_path: Path) -> None:
        """_chain_modes exits immediately when completed_mode is terminal."""
        from factory.cli.ceo import _chain_modes

        registry = {
            "swebench": _terminal_workflow(),
            "improve": _non_terminal_workflow(),
        }
        with patch("factory.workflow.definitions.register_all", return_value=registry):
            result = _chain_modes(tmp_path, completed_mode="swebench")
        assert result == 0

    def test_does_not_call_run_single_cycle_for_terminal(self, tmp_path: Path) -> None:
        """Terminal mode prevents any further cycle execution."""
        from factory.cli.ceo import _chain_modes

        registry = {"swebench": _terminal_workflow()}
        with patch("factory.workflow.definitions.register_all", return_value=registry), \
             patch("factory.cli.ceo._run_single_cycle") as mock_run:
            _chain_modes(tmp_path, completed_mode="swebench")
        mock_run.assert_not_called()

    def test_non_terminal_mode_proceeds(self, tmp_path: Path) -> None:
        """Non-terminal completed_mode does not short-circuit."""
        from factory.cli.ceo import _chain_modes

        registry = {"improve": _non_terminal_workflow()}
        with patch("factory.workflow.definitions.register_all", return_value=registry), \
             patch("factory.state.detect_state", return_value=ProjectState.HAS_FACTORY), \
             patch("factory.cli.ceo._auto_detect_mode", return_value="improve"), \
             patch("factory.cli.ceo._run_single_cycle", return_value=0):
            result = _chain_modes(
                tmp_path, completed_mode="improve", already_improved=True,
            )
        assert result == 0

    def test_no_completed_mode_proceeds(self, tmp_path: Path) -> None:
        """Without completed_mode, _chain_modes runs normally."""
        from factory.cli.ceo import _chain_modes

        with patch("factory.state.detect_state", return_value=ProjectState.HAS_FACTORY), \
             patch("factory.cli.ceo._auto_detect_mode", return_value="improve"), \
             patch("factory.cli.ceo._run_single_cycle", return_value=0):
            result = _chain_modes(tmp_path, already_improved=True)
        assert result == 0
