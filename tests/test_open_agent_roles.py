"""Tests for open agent roles — AgentRole | str union type, three-tier resolution,
sandbox default, JSON roundtrip, pool fallback, and existing workflow regression."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    AgentRole,
    DEFAULT_AGENT_POOL,
    GateNode,
    _role_str,
)


class TestRoleStrHelper:
    def test_enum_returns_value(self) -> None:
        assert _role_str(AgentRole.RESEARCHER) == "researcher"

    def test_string_passes_through(self) -> None:
        assert _role_str("security_auditor") == "security_auditor"


class TestAgentRoleUnionType:
    def test_agent_node_accepts_enum(self) -> None:
        node = AgentNode(id="r", role=AgentRole.RESEARCHER)
        assert node.role == AgentRole.RESEARCHER

    def test_agent_node_accepts_string(self) -> None:
        node = AgentNode(id="s", role="security_auditor")
        assert node.role == "security_auditor"

    def test_agent_config_accepts_enum(self) -> None:
        cfg = AgentConfig(role=AgentRole.BUILDER, model="opus")
        assert cfg.role == AgentRole.BUILDER

    def test_agent_config_accepts_string(self) -> None:
        cfg = AgentConfig(role="security_auditor", model="sonnet")
        assert cfg.role == "security_auditor"

    def test_gate_node_evaluator_role_accepts_string(self) -> None:
        gate = GateNode(id="g", evaluator_type="agent", evaluator_role="custom_reviewer")
        assert gate.evaluator_role == "custom_reviewer"

    def test_gate_node_evaluator_role_accepts_enum(self) -> None:
        gate = GateNode(id="g", evaluator_type="agent", evaluator_role=AgentRole.CEO)
        assert gate.evaluator_role == AgentRole.CEO

    def test_gate_node_evaluator_role_accepts_none(self) -> None:
        gate = GateNode(id="g", evaluator_type="fn")
        assert gate.evaluator_role is None

    def test_pydantic_validation_survives(self) -> None:
        node = AgentNode(id="x", role="my_custom_agent", prompt_template="do stuff")
        data = node.model_dump()
        restored = AgentNode.model_validate(data)
        assert restored.role == "my_custom_agent"


class TestThreeTierPromptResolution:
    def test_project_override_found_first(self, tmp_path: Path) -> None:
        from factory.agents.runner import resolve_prompt

        project = tmp_path / "proj"
        project.mkdir()
        agents_dir = project / ".factory" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "custom_role.md").write_text("# Project prompt")

        user_dir = tmp_path / "user_agents"
        user_dir.mkdir()
        (user_dir / "custom_role.md").write_text("# User prompt")

        with patch("factory.agents.runner._USER_AGENTS_DIR", user_dir):
            prompt = resolve_prompt("custom_role", project)
        assert "Project prompt" in prompt

    def test_user_override_found_second(self, tmp_path: Path) -> None:
        from factory.agents.runner import resolve_prompt

        project = tmp_path / "proj"
        project.mkdir()

        user_dir = tmp_path / "user_agents"
        user_dir.mkdir()
        (user_dir / "custom_role.md").write_text("# User prompt")

        with patch("factory.agents.runner._USER_AGENTS_DIR", user_dir):
            prompt = resolve_prompt("custom_role", project)
        assert "User prompt" in prompt

    def test_builtin_found_third(self) -> None:
        from factory.agents.runner import resolve_prompt

        prompt = resolve_prompt("researcher")
        assert len(prompt) > 0

    def test_all_miss_raises_with_paths(self, tmp_path: Path) -> None:
        from factory.agents.runner import resolve_prompt

        project = tmp_path / "proj"
        project.mkdir()
        user_dir = tmp_path / "nonexistent_agents"

        with patch("factory.agents.runner._USER_AGENTS_DIR", user_dir):
            with pytest.raises(FileNotFoundError, match="Searched:"):
                resolve_prompt("totally_unknown_role_xyz", project)


class TestSandboxModeDefault:
    def test_custom_role_returns_workspace_write(self) -> None:
        from factory.agents.plugin import _sandbox_mode

        assert _sandbox_mode("custom_agent") == "workspace-write"

    def test_builtin_roles_unchanged(self) -> None:
        from factory.agents.plugin import _READ_ONLY_ROLES, _WORKSPACE_WRITE_ROLES, _sandbox_mode

        for role in _READ_ONLY_ROLES:
            assert _sandbox_mode(role) == "read-only"
        for role in _WORKSPACE_WRITE_ROLES:
            assert _sandbox_mode(role) == "workspace-write"


class TestToolModeJsonRoundtrip:
    def test_agent_node_with_custom_role_survives_json(self) -> None:
        from factory.workflow.tool import _rebuild_workflow

        cache_data = {
            "name": "test",
            "start_node": "custom",
            "nodes": {
                "custom": {
                    "type": "AgentNode",
                    "id": "custom",
                    "role": "security_auditor",
                    "model": "sonnet",
                    "prompt_template": "audit security",
                    "timeout": 300,
                    "max_iterations": 1,
                    "blocking": True,
                    "reads": [],
                    "writes": [],
                },
            },
            "edges": [],
        }
        wf = _rebuild_workflow(cache_data)
        rebuilt_node = wf.nodes["custom"]
        assert isinstance(rebuilt_node, AgentNode)
        assert rebuilt_node.role == "security_auditor"

    def test_gate_node_with_custom_evaluator_role(self) -> None:
        from factory.workflow.tool import _rebuild_workflow

        cache_data = {
            "name": "test",
            "start_node": "gate",
            "nodes": {
                "gate": {
                    "type": "GateNode",
                    "id": "gate",
                    "evaluator_type": "agent",
                    "evaluator_role": "custom_reviewer",
                    "evaluator_command": None,
                    "gate_prompt": "review it",
                    "blocking": True,
                    "reads": [],
                    "writes": [],
                },
            },
            "edges": [],
        }
        wf = _rebuild_workflow(cache_data)
        gate = wf.nodes["gate"]
        assert isinstance(gate, GateNode)
        assert gate.evaluator_role == "custom_reviewer"

    def test_builtin_role_roundtrips_as_enum(self) -> None:
        from factory.workflow.tool import _rebuild_workflow

        cache_data = {
            "name": "test",
            "start_node": "r",
            "nodes": {
                "r": {
                    "type": "AgentNode",
                    "id": "r",
                    "role": "researcher",
                    "model": "sonnet",
                    "prompt_template": "",
                    "timeout": 600,
                    "max_iterations": 1,
                    "blocking": True,
                    "reads": [],
                    "writes": [],
                },
            },
            "edges": [],
        }
        wf = _rebuild_workflow(cache_data)
        assert wf.nodes["r"].role == AgentRole.RESEARCHER


class TestDefaultPoolFallback:
    def test_unknown_role_not_in_pool(self) -> None:
        assert DEFAULT_AGENT_POOL.get("security_auditor") is None

    def test_known_role_in_pool(self) -> None:
        assert DEFAULT_AGENT_POOL.get("researcher") is not None
        assert DEFAULT_AGENT_POOL["researcher"].model == "sonnet"

    def test_agent_config_with_custom_role(self) -> None:
        cfg = AgentConfig(role="custom_agent", model="sonnet", timeout=600)
        assert cfg.model == "sonnet"
        assert cfg.timeout == 600


class TestExistingWorkflowsUnchanged:
    def test_all_registered_workflows_validate(self) -> None:
        from factory.workflow.definitions import register_all

        all_wf = register_all()
        for name, wf in all_wf.items():
            issues = wf.validate_graph()
            assert issues == [], f"workflow '{name}' has validation issues: {issues}"

    def test_all_builtin_nodes_use_agent_role_enum(self) -> None:
        from factory.workflow.definitions import register_all

        all_wf = register_all()
        for name, wf in all_wf.items():
            for nid, node in wf.nodes.items():
                if isinstance(node, AgentNode):
                    assert isinstance(node.role, AgentRole), (
                        f"workflow '{name}' node '{nid}' uses string role '{node.role}' "
                        f"instead of AgentRole enum"
                    )
