"""`factory contained --target openshell` — plan composition, policy, dry run, checks.

The discipline from the k8s tests carries over: never reach a real gateway. Client
construction, the SDK, and the CLI are patched at their seams (`openshell.connect`,
`openshell.run_cli`, `import_sdk`), the same way `test_contained_k8s.py` stubs
`list_contexts` rather than shelling out to a real `oc`.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.cli import contained as cli
from factory.contained import openshell
from factory.contained.errors import ContainedError
from factory.contained.openshell_prereq import openshell_checks


def parse(argv: list[str]) -> argparse.Namespace:
    """Parse a `factory contained ...` command line the way the real CLI does."""
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    return parser.parse_args(["contained", *argv])


def interpret(argv: list[str]) -> argparse.Namespace:
    args = parse(argv)
    cli.interpret(cli._PARSER, args)
    return args


# A stand-in for the generated proto messages, so policy tests run without the optional SDK.
@dataclass(frozen=True)
class _FakeRule:
    name: str
    endpoints: list = field(default_factory=list)
    binaries: list = field(default_factory=list)


@dataclass
class _FakePolicy:
    version: int = 1
    filesystem: object = None
    process: object = None
    network_policies: dict = field(default_factory=dict)
    loaded: dict | None = None       # set by the json_format fake in the replacement test


class _FakePb2:
    """The surface `build_default_policy` touches, shaped like the real sandbox_pb2."""

    NetworkEndpoint = dict
    NetworkBinary = dict
    NetworkPolicyRule = _FakeRule
    FilesystemPolicy = dict
    ProcessPolicy = dict
    SandboxPolicy = _FakePolicy


def _fake_protos():
    class _OpenShellPb2:
        pass

    return _OpenShellPb2, _FakePb2


# --------------------------------------------------------------------------------------------
# Command surface
# --------------------------------------------------------------------------------------------


def test_the_target_is_a_choice() -> None:
    args = interpret(["--target", "openshell", "--", "study", "/tmp"])
    assert args.target == "openshell"
    assert args.policy is None
    assert args.gateway is None


def test_policy_flag_is_rejected_outside_openshell() -> None:
    with pytest.raises(SystemExit):
        interpret(["--policy", "p.yaml", "--", "study", "/tmp"])


def test_gateway_flag_is_rejected_outside_openshell() -> None:
    with pytest.raises(SystemExit):
        interpret(["--gateway", "prod", "--", "study", "/tmp"])


def test_division_is_refused_on_openshell() -> None:
    """The build plane is egress this target's policy exists to deny; refused at parse time,
    not three provisioning steps in."""
    with pytest.raises(SystemExit):
        interpret(["--target", "openshell", "--division", "--", "study", "/tmp"])


def test_local_flags_are_rejected_on_openshell() -> None:
    with pytest.raises(SystemExit):
        interpret(["--target", "openshell", "--mount", "/tmp", "--", "study", "/tmp"])


# --------------------------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------------------------


def test_the_default_policy_grants_exactly_the_intended_egress() -> None:
    """The allowlist is the security core; a test that enumerates it is the review artifact.
    Anyone adding an endpoint here changes what untrusted code can reach, and this failing
    diff is where that fact becomes visible."""
    with patch.object(openshell, "import_protos", _fake_protos):
        policy = openshell.build_default_policy()

    assert policy.version == 1
    rules = policy.network_policies
    assert set(rules) == {"claude_code", "python_packages"}

    claude = rules["claude_code"]
    claude_hosts = {e["host"] for e in claude.endpoints}
    assert claude_hosts == {"api.anthropic.com", "statsig.anthropic.com", "sentry.io"}
    assert all(e["port"] == 443 for e in claude.endpoints)
    assert {b["path"] for b in claude.binaries} == {"/usr/local/bin/claude", "/usr/bin/claude"}

    pypi = rules["python_packages"]
    assert {e["host"] for e in pypi.endpoints} == {"pypi.org", "files.pythonhosted.org"}
    assert all(e["port"] == 443 for e in pypi.endpoints)
    assert {b["path"] for b in pypi.binaries} == {
        "/usr/local/bin/pip", "/usr/bin/pip", "/usr/local/bin/uv", "/usr/bin/uv",
    }

    # The workspace is writable; the process is not root.
    assert policy.process == {"run_as_user": "1001", "run_as_group": "0"}


def test_a_policy_file_replaces_the_default_never_merges() -> None:
    """WYSIWYG: the file is the whole policy. The one rename between the documented YAML
    schema and the proto is applied, so users write what the OpenShell docs show."""
    import yaml

    document = {
        "version": 1,
        "filesystem_policy": {"include_workdir": True, "read_only": ["/usr"]},
        "process": {"run_as_user": "1500"},
    }
    path = Path("/tmp/does-not-matter.yaml")

    import types

    class _JsonFormat:
        ParseError = ValueError

        @staticmethod
        def ParseDict(translated, policy):
            assert translated["filesystem"] == document["filesystem_policy"]
            assert "filesystem_policy" not in translated
            policy.loaded = translated
            return policy

    fake_json_format = _JsonFormat
    with patch.object(openshell, "import_protos", _fake_protos), \
         patch.dict(
             "sys.modules",
             {
                 "google": types.ModuleType("google"),
                 "google.protobuf": types.ModuleType("google.protobuf"),
                 "google.protobuf.json_format": fake_json_format,
             },
         ), \
         patch.object(Path, "read_text", return_value=yaml.safe_dump(document)), \
         patch.object(Path, "is_file", return_value=True):
        policy = openshell.load_policy(path)

    assert policy.loaded["process"] == {"run_as_user": "1500"}


def test_a_malformed_policy_file_is_an_error_naming_the_file() -> None:
    path = Path("/tmp/policy.yaml")
    with patch.object(openshell, "import_protos", _fake_protos), \
         patch.object(Path, "read_text", return_value="just a string"), \
         patch.object(Path, "is_file", return_value=True):
        with pytest.raises(ContainedError, match="policy file"):
            openshell.load_policy(path)


# --------------------------------------------------------------------------------------------
# Plan composition and dry run
# --------------------------------------------------------------------------------------------


def _plan_args(project: Path, *flags: str) -> argparse.Namespace:
    args = parse(["--target", "openshell", *flags, "--", "study", str(project)])
    cli.interpret(cli._PARSER, args)
    return args


def _workspace(project: Path, run_id: str = "rta-abc123"):
    from factory.contained.workspace import plan_workspace

    return plan_workspace(project, run_id, self_contained=True)


def test_dry_run_prints_the_real_steps_and_provisions_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same contract as the other targets: dry-run prints what the real path would execute —
    the SDK operations as described steps, the CLI parts as exact argv — and contacts
    nothing, not even a gateway."""
    from factory.cli.contained_openshell import run_openshell

    project = tmp_path / "rta"
    project.mkdir()
    args = _plan_args(project)

    with patch.dict(os.environ, {"FACTORY_CONTAINED_DRY_RUN": "1"}, clear=False):
        code = run_openshell(args)

    assert code == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "nothing is provisioned" in out
    assert "factory default" in out                       # states which policy applies
    assert "openshell sandbox upload" in out              # exact CLI argv for the transfer
    for step in ("[create]", "[upload]", "[extract]", "[assert:workdir]", "[run]"):
        assert step in out, f"dry run does not show the {step} step"


def test_the_environment_carries_configuration_and_names_secret_smuggling(
    tmp_path: Path,
) -> None:
    """The provider is the only supported credential route; a secret-looking key in the plan
    env means --forward/--env was used to smuggle one past that boundary, and the plan warns
    rather than quietly forwarding it."""
    from factory.cli.contained_openshell import _build_plan

    project = tmp_path / "rta"
    project.mkdir()
    ws = _workspace(project)
    args = _plan_args(project, "--env", "ANTHROPIC_API_KEY=sk-ant-smuggled")

    plan = _build_plan(args, ws, "rta-abc123", {"ANTHROPIC_API_KEY": "sk-ant-smuggled"}, {})

    assert plan.env["ANTHROPIC_API_KEY"] == "sk-ant-smuggled"   # escape hatch still wins
    assert any("provider" in warning for warning in plan.warnings)


def test_a_secret_forwarded_into_the_plan_is_redacted_in_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run output is a log line and an evidence file as often as a terminal; a real key
    must never appear in it."""
    from factory.cli.contained_openshell import run_openshell

    project = tmp_path / "rta"
    project.mkdir()
    args = _plan_args(project, "--env", "ANTHROPIC_API_KEY=sk-ant-real-value")

    with patch.dict(os.environ, {"FACTORY_CONTAINED_DRY_RUN": "1"}, clear=False):
        run_openshell(args)

    out = capsys.readouterr().out
    assert "sk-ant-real-value" not in out
    assert "ANTHROPIC_API_KEY=<redacted>" in out


def test_policy_path_resolves_and_a_missing_file_fails_before_any_copy(tmp_path: Path) -> None:
    from factory.cli.contained_openshell import _build_plan

    project = tmp_path / "rta"
    project.mkdir()
    ws = _workspace(project)
    args = _plan_args(project, "--policy", str(tmp_path / "absent.yaml"))

    with pytest.raises(ContainedError, match="no such file"):
        _build_plan(args, ws, "rta-abc123", {}, {})


def test_labels_carry_the_factory_join_keys(tmp_path: Path) -> None:
    """`ls` selects on `factory.contained=true`; the project hash and source path make the
    listing useful without reaching into the gateway again."""
    from factory.cli.contained_openshell import _build_plan

    project = tmp_path / "rta"
    project.mkdir()
    ws = _workspace(project)
    args = _plan_args(project)

    plan = _build_plan(args, ws, "rta-abc123", {}, {})

    assert plan.labels["factory.contained"] == "true"
    assert plan.labels["factory.name"] == "rta-abc123"
    assert plan.labels["factory.source"] == str(project)
    assert plan.labels["factory.project"]
    assert plan.project_dir == f"{openshell.WORKSPACE_ROOT}/rta"


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------


def test_every_check_degrades_to_a_fix_carrying_failure(tmp_path: Path) -> None:
    """A machine with nothing installed gets a list of what is missing, not a traceback."""
    with patch("shutil.which", return_value=None), \
         patch("factory.contained.openshell.connect", side_effect=Exception("no gateway")):
        checks = openshell_checks()

    assert [c.name for c in checks] == [
        "openshell_cli", "openshell_sdk", "openshell_gateway", "runtime_image",
        "openshell_provider",
    ]
    for check in checks:
        if not check.ok:
            assert check.fix, f"{check.name} fails without a fix"


# --------------------------------------------------------------------------------------------
# Listing and lifecycle seams
# --------------------------------------------------------------------------------------------


def test_listing_selects_on_the_factory_label_and_maps_phases() -> None:
    @dataclass
    class _Status:
        phase: int = 2
        exit_code: int | None = None

    @dataclass
    class _Ref:
        name: str
        workspace: str = "default"
        status: _Status = field(default_factory=_Status)
        labels: dict = field(default_factory=dict)

        @property
        def phase(self) -> int:
            return self.status.phase

    class _Pager:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _Client:
        def list(self, **kwargs):
            assert kwargs["label_selector"] == "factory.contained=true"
            return _Pager([
                _Ref(name="rta-abc123", labels={"factory.project": "abc", "factory.source": "/p"}),
                _Ref(name="other", labels={"factory.project": "x"}),
            ])

    with patch.object(openshell, "connect", return_value=_Client()):
        entries = openshell.list_runtimes()

    assert entries[0]["name"] == "rta-abc123"
    assert entries[0]["phase"] == "running"
    assert entries[0]["project"] == "abc"


def test_a_launch_failure_is_distinguished_from_a_command_failure() -> None:
    """`exec` reports -1 when the command did not run at all; the caller's message must not
    claim the probe executed and failed."""
    from factory.contained.openshell import exec_argv

    @dataclass
    class _Result:
        exit_code: int
        stdout: str = ""
        stderr: str = ""

    class _Session:
        def exec(self, argv, **kwargs):
            return _Result(exit_code=-1)

    with pytest.raises(ContainedError, match="did not execute at all"):
        exec_argv(_Session(), ["pwd"])


# --------------------------------------------------------------------------------------------
# Lifecycle routing
# --------------------------------------------------------------------------------------------


def test_lifecycle_subcommands_route_to_the_openshell_handlers() -> None:
    """attach/rm/sync take their target from --target; the handlers are reached with the
    gateway and confirmation flags passed through."""
    import factory.contained.lifecycle as lifecycle_mod
    from factory.contained.lifecycle import dispatch_lifecycle

    for subcommand, handler_name in (
        ("attach", "attach"), ("rm", "remove"), ("sync", "sync")
    ):
        args = argparse.Namespace(
            subcommand=subcommand,
            target="openshell",
            name="rta-abc123",
            namespace=None,
            gateway="gw",
            yes=True,
        )
        with patch.object(lifecycle_mod, handler_name, return_value=0) as handler:
            assert dispatch_lifecycle(args) == 0
        handler.assert_called_once()
        # The gateway rides along either positionally (attach/sync) or as a keyword (remove,
        # whose signature keeps `assume_yes`/`interactive` keyword-only after it).
        call = handler.call_args
        assert call.args[:3] == ("rta-abc123", "openshell", None)
        assert call.kwargs.get("gateway", call.args[3] if len(call.args) > 3 else None) == "gw"


def test_rm_deletes_the_sandbox_and_keeps_the_workspace_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting the sandbox must not delete the local workspace copy — the work is kept, and
    the merge hint says how to get it back."""
    from factory.contained.lifecycle import remove
    from factory.contained.runtimes import Runtime

    runtime = Runtime(
        name="rta-abc123", target="openshell", project="abc", state="finished"
    )
    with patch("factory.contained.lifecycle.list_runtimes", return_value=([runtime], [], [])), \
         patch("factory.contained.openshell.remove_runtime") as deleter, \
         patch("factory.contained.lifecycle.workspace_for", return_value=None):
        assert remove("rta-abc123", "openshell", assume_yes=True) == 0
    deleter.assert_called_once_with("rta-abc123", gateway=None)
    assert "sandbox deleted" in capsys.readouterr().out

