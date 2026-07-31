"""Tests for sandbox mode: environment policy inversion and the `--bare` scoping.

The two behaviors here are the ones that fail quietly. Forwarding Vertex configuration into a
sandbox produces a network-policy denial that reads like an infrastructure fault, and omitting
`--bare` produces an OAuth prompt nothing inside a sandbox can answer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from factory.cli._run_args import (
    SANDBOX_ENV_POLICY,
    TMUX_ENV_POLICY,
    EnvPolicy,
    build_env_exports,
    build_run_args,
)
from factory.models import AgentRunRequest
from factory.runners.claude import ClaudeRunner
from factory.sandbox import (
    SANDBOX_ENV_VAR,
    SANDBOX_INFERENCE_API_KEY,
    SANDBOX_INFERENCE_BASE_URL,
    in_sandbox,
)

HOST_ENV = {
    "FACTORY_MODEL": "opus",
    "FACTORY_MANAGED_DIRS": "/work/managed",
    "FACTORY_VAULT_PATH": "/work/vault",
    "ANTHROPIC_API_KEY": "sk-ant-real-credential",
    "ANTHROPIC_VERTEX_PROJECT_ID": "itpc-gcp-ai-eng-claude",
    "BOBSHELL_API_KEY": "bob-key",
    "OPENAI_API_KEY": "sk-openai",
    "CODEX_API_KEY": "codex-key",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLOUD_ML_REGION": "us-east5",
    "HOME": "/home/user",
    "UNRELATED_VAR": "should-not-appear",
}


class TestInSandbox:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
    def test_truthy_values(self, value: str) -> None:
        assert in_sandbox({SANDBOX_ENV_VAR: value}) is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_falsy_values(self, value: str) -> None:
        assert in_sandbox({SANDBOX_ENV_VAR: value}) is False

    def test_absent_is_false(self) -> None:
        assert in_sandbox({}) is False


class TestTmuxEnvPolicy:
    """tmux runs on the caller's machine, so the caller's inference setup must survive."""

    def test_forwards_vertex_configuration(self) -> None:
        resolved = TMUX_ENV_POLICY.resolve(HOST_ENV)
        assert resolved["CLAUDE_CODE_USE_VERTEX"] == "1"
        assert resolved["CLOUD_ML_REGION"] == "us-east5"

    def test_forwards_anthropic_key_unchanged(self) -> None:
        assert TMUX_ENV_POLICY.resolve(HOST_ENV)["ANTHROPIC_API_KEY"] == "sk-ant-real-credential"

    def test_excludes_unprefixed_variables(self) -> None:
        resolved = TMUX_ENV_POLICY.resolve(HOST_ENV)
        assert "UNRELATED_VAR" not in resolved
        assert "HOME" not in resolved

    def test_does_not_mark_the_process_as_contained(self) -> None:
        assert SANDBOX_ENV_VAR not in TMUX_ENV_POLICY.resolve(HOST_ENV)


class TestSandboxEnvPolicy:
    """A sandbox reaches inference through the gateway, so the same variables must not cross."""

    def test_strips_vertex_configuration(self) -> None:
        resolved = SANDBOX_ENV_POLICY.resolve(HOST_ENV)
        assert "CLAUDE_CODE_USE_VERTEX" not in resolved
        assert "CLOUD_ML_REGION" not in resolved

    def test_strips_forwarded_anthropic_credentials(self) -> None:
        """Credentials live on the gateway (spec §8); the sandbox gets a placeholder."""
        resolved = SANDBOX_ENV_POLICY.resolve(HOST_ENV)
        assert resolved["ANTHROPIC_API_KEY"] == SANDBOX_INFERENCE_API_KEY
        assert "ANTHROPIC_VERTEX_PROJECT_ID" not in resolved

    def test_pins_base_url_without_v1_suffix(self) -> None:
        """Claude Code appends /v1/messages itself; a doubled path fails obscurely."""
        base = SANDBOX_ENV_POLICY.resolve(HOST_ENV)["ANTHROPIC_BASE_URL"]
        assert base == SANDBOX_INFERENCE_BASE_URL == "https://inference.local"
        assert not base.endswith("/v1")
        assert not base.endswith("/")

    def test_substitution_overrides_a_forwarded_value(self) -> None:
        policy = EnvPolicy(
            forward_prefixes=("ANTHROPIC_",), substitutions=(("ANTHROPIC_API_KEY", "unused"),)
        )
        assert policy.resolve({"ANTHROPIC_API_KEY": "real"})["ANTHROPIC_API_KEY"] == "unused"

    def test_marks_the_process_as_contained(self) -> None:
        assert SANDBOX_ENV_POLICY.resolve(HOST_ENV)[SANDBOX_ENV_VAR] == "1"

    def test_forwards_growth_context(self) -> None:
        """Growth dimensions merge 50/50 into the composite; losing them changes every score."""
        resolved = SANDBOX_ENV_POLICY.resolve(HOST_ENV)
        assert resolved["FACTORY_MANAGED_DIRS"] == "/work/managed"
        assert resolved["FACTORY_VAULT_PATH"] == "/work/vault"


class TestBuildEnvExports:
    def test_sorted_by_key(self) -> None:
        exports = build_env_exports(HOST_ENV, TMUX_ENV_POLICY)
        keys = [line.split("=", 1)[0].removeprefix("export ") for line in exports]
        assert keys == sorted(keys)

    def test_quotes_values_needing_it(self) -> None:
        exports = build_env_exports({"FACTORY_FOCUS": "two words"}, TMUX_ENV_POLICY)
        assert exports == ["export FACTORY_FOCUS='two words'"]


class TestBuildRunArgs:
    @staticmethod
    def _args(**overrides: object) -> argparse.Namespace:
        base: dict[str, object] = {
            "mode": "improve",
            "no_github": False,
            "profile": None,
            "focus": None,
            "refine": None,
            "clean_pr": None,
            "runner": None,
            "prompt": None,
            "branch": None,
            "min_growth": None,
            "max_new": None,
            "discover_only": False,
            "bg_agents": False,
            "tmux_persist": False,
            "use_profile": False,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_minimal_invocation(self) -> None:
        assert build_run_args(self._args(), Path("/p"), None) == "factory ceo /p --mode improve"

    def test_quotes_multiword_focus(self) -> None:
        composed = build_run_args(self._args(focus="eval reliability"), Path("/p"), None)
        assert "--focus 'eval reliability'" in composed

    def test_clean_pr_tristate(self) -> None:
        assert "--clean-pr" in build_run_args(self._args(clean_pr=True), Path("/p"), None)
        assert "--no-clean-pr" in build_run_args(self._args(clean_pr=False), Path("/p"), None)
        composed = build_run_args(self._args(clean_pr=None), Path("/p"), None)
        assert "clean-pr" not in composed


class TestBareFlag:
    @staticmethod
    def _request(tmp_path: Path) -> AgentRunRequest:
        return AgentRunRequest(prompt="p", task="t", cwd=tmp_path, role="researcher")

    def test_present_in_sandbox_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        cmd, _env, temp_files = ClaudeRunner().build_command(self._request(tmp_path))
        try:
            assert "--bare" in cmd
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def test_absent_outside_sandbox_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        cmd, _env, temp_files = ClaudeRunner().build_command(self._request(tmp_path))
        try:
            assert "--bare" not in cmd
        finally:
            for f in temp_files:
                f.unlink(missing_ok=True)

    def test_interactive_command_is_scoped_the_same_way(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = ClaudeRunner()
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        cmd, _env, temp_files = runner.build_interactive_command(self._request(tmp_path))
        for f in temp_files:
            f.unlink(missing_ok=True)
        assert "--bare" in cmd

        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        cmd, _env, temp_files = runner.build_interactive_command(self._request(tmp_path))
        for f in temp_files:
            f.unlink(missing_ok=True)
        assert "--bare" not in cmd
