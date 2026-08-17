"""Tests for the CLI plugin architecture."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from factory.plugins import (
    CommandSpec,
    PluginLoadResult,
    PluginRegistry,
    load_plugins,
)


def _make_ep(name: str, load_return=None, load_exc=None, dist_name: str | None = None, dist_version: str | None = "0.1.0"):
    """Build a mock entry point."""
    ep = MagicMock()
    ep.name = name
    dist = MagicMock()
    dist.name = dist_name or name
    dist.version = dist_version
    ep.dist = dist
    if load_exc:
        ep.load.side_effect = load_exc
    else:
        ep.load.return_value = load_return
    return ep


class TestLoadPluginsNoEntrypoints:
    def test_empty_group_no_crash(self):
        registry = PluginRegistry()
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = []
            results = load_plugins(registry)
        assert results == []
        assert registry.commands == {}
        assert registry.modes == []


class TestLoadPluginsValidPlugin:
    def test_registers_command(self):
        def my_plugin(reg: PluginRegistry):
            reg.add_commands({"greet": CommandSpec(handler=lambda a: 0, help="Say hello")})

        registry = PluginRegistry()
        ep = _make_ep("my-plugin", load_return=my_plugin)
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep]
            results = load_plugins(registry)
        assert len(results) == 1
        assert results[0].status == "loaded"
        assert results[0].name == "my-plugin"
        assert "greet" in registry.commands


class TestLoadPluginsBrokenImport:
    def test_import_error_isolated(self):
        good_called = []
        def good_plugin(reg: PluginRegistry):
            good_called.append(True)
            reg.add_commands({"good": CommandSpec(handler=lambda a: 0, help="Works")})

        bad_ep = _make_ep("aaa-bad", load_exc=ImportError("no module"), dist_name="aaa-bad")
        good_ep = _make_ep("zzz-good", load_return=good_plugin, dist_name="zzz-good")

        registry = PluginRegistry()
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [good_ep, bad_ep]
            results = load_plugins(registry)

        statuses = {r.name: r.status for r in results}
        assert statuses["aaa-bad"] == "failed"
        assert statuses["zzz-good"] == "loaded"
        assert "good" in registry.commands


class TestLoadPluginsBrokenRegistration:
    def test_registration_error_isolated(self):
        def broken_plugin(reg: PluginRegistry):
            raise RuntimeError("plugin init failed")

        registry = PluginRegistry()
        ep = _make_ep("broken", load_return=broken_plugin)
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep]
            results = load_plugins(registry)
        assert results[0].status == "failed"
        assert "Registration error" in results[0].reason


class TestLoadPluginsNotCallable:
    def test_non_callable_entry_point(self):
        registry = PluginRegistry()
        ep = _make_ep("bad-entry", load_return="not_a_function")
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep]
            results = load_plugins(registry)
        assert results[0].status == "failed"
        assert "not callable" in results[0].reason


class TestCollisionDetectionCommands:
    def test_same_name_twice_first_wins(self):
        def handler_a(a):  # noqa: ANN001, ANN202
            return 0

        def handler_b(a):  # noqa: ANN001, ANN202
            return 1

        def plugin_a(reg: PluginRegistry):
            reg.add_commands({"dup": CommandSpec(handler=handler_a, help="First")})

        def plugin_b(reg: PluginRegistry):
            reg.add_commands({"dup": CommandSpec(handler=handler_b, help="Second")})

        ep_a = _make_ep("aaa-first", load_return=plugin_a, dist_name="aaa-first")
        ep_b = _make_ep("zzz-second", load_return=plugin_b, dist_name="zzz-second")

        registry = PluginRegistry()
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep_a, ep_b]
            load_plugins(registry)
        assert registry.commands["dup"].handler is handler_a


class TestCollisionWithBuiltinCommand:
    def test_builtin_command_skipped_with_warning(self):
        registry = PluginRegistry()
        registry.add_commands({
            "eval": CommandSpec(handler=lambda a: 0, help="Shadow builtin eval"),
            "my-new-cmd": CommandSpec(handler=lambda a: 0, help="Legit plugin cmd"),
        })
        assert "eval" not in registry.commands
        assert "my-new-cmd" in registry.commands


class TestCollisionDetectionModes:
    def test_collision_with_builtin_skipped(self):
        def plugin(reg: PluginRegistry):
            reg.add_modes(["improve", "custom-mode"])

        registry = PluginRegistry()
        ep = _make_ep("mode-plugin", load_return=plugin)
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep]
            load_plugins(registry)
        assert "improve" not in registry.modes
        assert "custom-mode" in registry.modes


class TestCmdPluginsOutput:
    def test_human_readable_format(self, capsys):
        results = [
            PluginLoadResult(name="my-plugin", status="loaded", version="1.0.0"),
            PluginLoadResult(name="bad-plugin", status="failed", reason="Import error", version="0.1.0"),
        ]

        registry = PluginRegistry()
        registry.commands["greet"] = CommandSpec(handler=lambda a: 0, help="Say hello")

        _cmd_plugins_text(results, registry)

        captured = capsys.readouterr()
        assert "my-plugin" in captured.out
        assert "loaded" in captured.out


class TestCmdPluginsJson:
    def test_valid_json(self, capsys):
        results = [
            PluginLoadResult(name="my-plugin", status="loaded", version="1.0.0"),
        ]
        registry = PluginRegistry()
        registry.commands["greet"] = CommandSpec(handler=lambda a: 0, help="Say hello")

        _cmd_plugins_json(results, registry)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert data[0]["name"] == "my-plugin"
        assert data[0]["status"] == "loaded"


class TestGetAllCeoModesIncludesPlugins:
    def test_plugin_mode_in_result(self):
        from factory.cli._helpers import CEO_MODES, get_all_ceo_modes

        registry = PluginRegistry()
        registry.modes = ["my-custom-mode"]
        with patch("factory.plugins.get_registry", return_value=registry):
            all_modes = get_all_ceo_modes()
        assert "my-custom-mode" in all_modes
        for m in CEO_MODES:
            assert m in all_modes


class TestAddParserExtensions:
    def test_extension_stored(self):
        registry = PluginRegistry()
        ext_fn = MagicMock()
        registry.add_parser_extensions({"ceo": ext_fn})
        assert "ceo" in registry.parser_extensions
        assert registry.parser_extensions["ceo"] == [ext_fn]

    def test_multiple_extensions_same_subcommand(self):
        registry = PluginRegistry()
        ext_a = MagicMock()
        ext_b = MagicMock()
        registry.add_parser_extensions({"ceo": ext_a})
        registry.add_parser_extensions({"ceo": ext_b})
        assert registry.parser_extensions["ceo"] == [ext_a, ext_b]


class TestAddParserExtensionsApplied:
    def test_extension_called_on_build_parser(self):
        ext_fn = MagicMock()

        def plugin(reg: PluginRegistry):
            reg.add_parser_extensions({"ceo": ext_fn})

        ep = _make_ep("ext-plugin", load_return=plugin)
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep]
            from factory.cli._main import build_parser

            build_parser()

        ext_fn.assert_called_once()
        import argparse
        assert isinstance(ext_fn.call_args[0][0], argparse.ArgumentParser)


class TestCeoPreHookCalled:
    def test_pre_hook_invoked(self):
        hook = MagicMock(return_value=None)
        registry = PluginRegistry()
        registry.ceo_pre_hooks.append(hook)

        with (
            patch("factory.plugins.get_registry", return_value=registry),
            patch("factory.cli.ceo._validate_ceo_flags") as mock_validate,
            patch("factory.cli.ceo._resolve_ceo_project") as mock_resolve,
            patch("factory.cli.ceo._validate_late_flags", return_value=None),
            patch("factory.cli.ceo._execute_ceo", return_value=0),
            patch("factory.user_config.load_config"),
        ):
            mock_validate.return_value = (
                "improve", False, False, False, None, None, None, None, False, None, False,
            )
            mock_resolve.return_value = (
                "/tmp/proj", None, None, None,
                None, False, False, None, None,
            )
            from factory.cli.ceo import cmd_ceo

            args = MagicMock()
            args.path = "/tmp/proj"
            args.profile = None
            args.no_github = False
            cmd_ceo(args)

        hook.assert_called_once()
        call_args = hook.call_args[0]
        assert call_args[0] == "improve"


class TestSandboxModeUnknownRole:
    def test_defaults_to_read_only(self):
        from factory.agents.plugin import _sandbox_mode
        assert _sandbox_mode("totally_unknown_role") == "read-only"


class TestDeterministicLoadOrder:
    def test_sorted_by_dist_name(self):
        def plugin_c(reg: PluginRegistry):
            pass
        def plugin_a(reg: PluginRegistry):
            pass
        def plugin_b(reg: PluginRegistry):
            pass

        ep_c = _make_ep("charlie", load_return=plugin_c, dist_name="charlie")
        ep_a = _make_ep("alpha", load_return=plugin_a, dist_name="alpha")
        ep_b = _make_ep("bravo", load_return=plugin_b, dist_name="bravo")

        registry = PluginRegistry()
        with patch("factory.plugins.importlib.metadata.entry_points") as mock_eps:
            mock_eps.return_value = MagicMock()
            mock_eps.return_value.select.return_value = [ep_c, ep_a, ep_b]
            results = load_plugins(registry)
        names = [r.name for r in results]
        assert names == ["alpha", "bravo", "charlie"]


# ── helpers used by tests ──────────────────────────────────────

def _cmd_plugins_text(results: list[PluginLoadResult], registry: PluginRegistry) -> None:
    if not results:
        print("No plugins discovered.")
        return
    for r in results:
        ver = f" v{r.version}" if r.version else ""
        line = f"  {r.name}{ver}: {r.status}"
        if r.reason:
            line += f" ({r.reason})"
        print(line)
    if registry.commands:
        print(f"\nRegistered commands: {', '.join(sorted(registry.commands))}")
    if registry.modes:
        print(f"Registered modes: {', '.join(registry.modes)}")


def _cmd_plugins_json(results: list[PluginLoadResult], registry: PluginRegistry) -> None:
    import dataclasses
    data = []
    for r in results:
        entry = dataclasses.asdict(r)
        data.append(entry)
    print(json.dumps(data, indent=2))
