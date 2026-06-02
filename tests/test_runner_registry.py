"""Tests for factory/runners/registry.py — RunnerRegistry."""

from unittest.mock import AsyncMock

import pytest

from factory.runners.registry import RunnerRegistry


class TestRunnerRegistry:
    def test_register_and_get(self) -> None:
        reg = RunnerRegistry()
        reg.register("test", lambda **kw: "test_runner")
        result = reg.get("test")
        assert result == "test_runner"

    def test_get_unknown_raises(self) -> None:
        reg = RunnerRegistry()
        reg.register("claude", lambda **kw: "claude_runner")
        with pytest.raises(ValueError, match="Unknown runner: nope"):
            reg.get("nope")

    def test_get_default_is_claude(self) -> None:
        reg = RunnerRegistry()
        reg.register("claude", lambda **kw: "claude_runner")
        result = reg.get()
        assert result == "claude_runner"

    def test_get_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = RunnerRegistry()
        reg.register("claude", lambda **kw: "claude_runner")
        reg.register("bob", lambda **kw: "bob_runner")
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        result = reg.get()
        assert result == "bob_runner"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = RunnerRegistry()
        reg.register("claude", lambda **kw: "claude_runner")
        reg.register("bob", lambda **kw: "bob_runner")
        monkeypatch.setenv("FACTORY_RUNNER", "bob")
        result = reg.get("claude")
        assert result == "claude_runner"

    def test_list_available(self) -> None:
        reg = RunnerRegistry()
        reg.register("codex", lambda **kw: None)
        reg.register("bob", lambda **kw: None)
        reg.register("claude", lambda **kw: None)
        assert reg.list_available() == ["bob", "claude", "codex"]

    def test_list_available_empty(self) -> None:
        reg = RunnerRegistry()
        assert reg.list_available() == []

    def test_get_passes_kwargs(self) -> None:
        reg = RunnerRegistry()
        reg.register("bob", lambda **kw: kw.get("project_path"))
        result = reg.get("bob", project_path="/tmp/proj")
        assert result == "/tmp/proj"

    def test_get_case_insensitive(self) -> None:
        reg = RunnerRegistry()
        reg.register("claude", lambda **kw: "ok")
        assert reg.get("CLAUDE") == "ok"
        assert reg.get(" Claude ") == "ok"

    def test_error_message_lists_available(self) -> None:
        reg = RunnerRegistry()
        reg.register("alice", lambda **kw: None)
        reg.register("bob", lambda **kw: None)
        with pytest.raises(ValueError, match="alice, bob"):
            reg.get("unknown")


class TestRunnerRegistryAsync:
    async def test_check_all_healthy(self) -> None:
        reg = RunnerRegistry()

        class FakeRunner:
            check_health = AsyncMock(return_value=(True, "installed"))

        reg.register("fake", lambda **kw: FakeRunner())
        results = await reg.check_all()
        assert "fake" in results
        ok, msg = results["fake"]
        assert ok is True
        assert msg == "installed"

    async def test_check_all_no_health_check(self) -> None:
        reg = RunnerRegistry()

        class NoHealthRunner:
            pass

        reg.register("simple", lambda **kw: NoHealthRunner())
        results = await reg.check_all()
        ok, msg = results["simple"]
        assert ok is True
        assert msg == "no health check"

    async def test_check_all_factory_error(self) -> None:
        reg = RunnerRegistry()
        reg.register("broken", lambda **kw: (_ for _ in ()).throw(RuntimeError("missing binary")))
        results = await reg.check_all()
        ok, msg = results["broken"]
        assert ok is False
        assert "missing binary" in msg

    async def test_check_all_health_fails(self) -> None:
        reg = RunnerRegistry()

        class UnhealthyRunner:
            check_health = AsyncMock(return_value=(False, "not installed"))

        reg.register("sick", lambda **kw: UnhealthyRunner())
        results = await reg.check_all()
        ok, msg = results["sick"]
        assert ok is False
        assert msg == "not installed"

    async def test_check_one(self) -> None:
        reg = RunnerRegistry()

        class FakeRunner:
            check_health = AsyncMock(return_value=(True, "ok"))

        reg.register("test", lambda **kw: FakeRunner())
        ok, msg = await reg.check_one("test")
        assert ok is True

    async def test_check_one_unknown_raises(self) -> None:
        reg = RunnerRegistry()
        with pytest.raises(ValueError, match="Unknown runner: nope"):
            await reg.check_one("nope")


class TestModuleLevelRegistry:
    def test_module_registry_has_claude(self) -> None:
        from factory.runners import _registry

        assert "claude" in _registry.list_available()

    def test_module_registry_has_bob(self) -> None:
        from factory.runners import _registry

        assert "bob" in _registry.list_available()

    def test_module_registry_has_codex(self) -> None:
        from factory.runners import _registry

        assert "codex" in _registry.list_available()
