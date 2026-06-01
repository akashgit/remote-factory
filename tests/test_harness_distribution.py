"""Tests for distribution bundle descriptors and emitters."""

from factory.agents.plugin import generate_agent_content, generate_codex_agent_toml, load_agent_config
from factory.harness.adapters import CurrentAgentDistributionAdapter
from factory.harness.distribution import (
    DistributionTarget,
    build_current_agent_package_spec,
    cli_local_bundle,
    emit_current_agent_role,
)


def test_cli_local_bundle_describes_current_primary_distribution():
    bundle = cli_local_bundle()

    assert bundle.name == "cli-local"
    assert bundle.surface == "factory CLI"
    assert bundle.runtime == "local_agent_runtime"
    assert bundle.state_backend == "local_factory_state"
    assert "precheck" in bundle.guardrails
    assert "claude_agent_files" in bundle.emitters


def test_current_agent_package_spec_matches_loaded_agent_config():
    config = load_agent_config()
    spec = build_current_agent_package_spec()

    assert set(spec.roles) == set(config)
    assert spec.roles["builder"].description == config["builder"].description
    assert spec.roles["builder"].tools == config["builder"].tools


def test_emit_current_agent_role_preserves_current_claude_output():
    assert emit_current_agent_role("builder", DistributionTarget.CLAUDE) == generate_agent_content(
        "builder"
    )


def test_emit_current_agent_role_preserves_current_codex_output():
    assert emit_current_agent_role(
        "researcher",
        DistributionTarget.CODEX,
    ) == generate_codex_agent_toml("researcher")


def test_current_distribution_adapter_preserves_outputs():
    assert CurrentAgentDistributionAdapter("claude").emit_role("builder") == generate_agent_content(
        "builder"
    )
    assert CurrentAgentDistributionAdapter("codex").emit_role(
        "builder"
    ) == generate_codex_agent_toml("builder")
