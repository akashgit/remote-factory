"""`factory contained --target openshell` — plan composition, policy, dry run, checks.

The discipline from the k8s tests carries over: never reach a real gateway. Client
construction, the SDK, and the CLI are patched at their seams (`openshell.connect`,
`openshell.run_cli`, `import_sdk`), the same way `test_contained_k8s.py` stubs
`list_contexts` rather than shelling out to a real `oc`.
"""

from __future__ import annotations

import argparse
import os
import time
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


def test_sandbox_names_fit_the_gateway_budget() -> None:
    """The gateway enforces names at create time as an INVALID_ARGUMENT; deriving a compliant
    name here turns that into something a user never sees. Long stems truncate, the hash never
    does, and illegal characters (underscore, dot, uppercase) collapse like podman's slugs."""
    from factory.contained.openshell import MAX_SANDBOX_NAME, sandbox_name

    short = sandbox_name(Path("/code/rta"))
    assert short.startswith("rta-") and len(short) <= MAX_SANDBOX_NAME

    long = sandbox_name(Path("/code/a-really-long-project-stem-here"))
    assert len(long) == MAX_SANDBOX_NAME
    # the hash suffix is never the truncated part: 6 hex chars survive at the end
    assert long[-7] == "-" and len(long.rsplit("-", 1)[-1]) == 6

    weird = sandbox_name(Path("/code/My_Project.v2"))
    assert len(weird) == MAX_SANDBOX_NAME
    assert weird.startswith("my-project-v-") and "._" not in weird

    # Non-ASCII letters are legal to `str.isalnum` and illegal to the gateway; they collapse.
    unicode_stem = sandbox_name(Path("/code/café"))
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in unicode_stem)


def test_user_supplied_names_are_validated_before_any_copy() -> None:
    """`--name` reaches the gateway verbatim; a name it would reject is a parse-time error,
    not a create-time traceback after the workspace was already copied."""
    from factory.contained.openshell import validate_sandbox_name

    assert validate_sandbox_name("fine-name") == "fine-name"
    assert validate_sandbox_name("run-1") == "run-1"      # digits are legal — a precedence bug
    # in an earlier formulation rejected them while the error message claimed otherwise
    assert validate_sandbox_name("run2") == "run2"
    assert validate_sandbox_name("") == ""
    with pytest.raises(ContainedError, match="at most 19"):
        validate_sandbox_name("a" * 20)
    with pytest.raises(ContainedError, match="lowercase alphanumeric"):
        validate_sandbox_name("Bad_Name")
    with pytest.raises(ContainedError, match="lowercase alphanumeric"):
        validate_sandbox_name("café")                     # non-ASCII is not in the gateway's set


def test_every_generated_name_passes_validation() -> None:
    """The invariant the review's precedence bug broke in spirit: `sandbox_name` output must
    always satisfy `validate_sandbox_name`, so a user can copy a name out of `ls` into `--name`
    (and a future change to either half of the alphabet cannot drift from the other)."""
    from factory.contained.openshell import sandbox_name, validate_sandbox_name

    for stem in ("rta", "My_Project.v2", "café", "a-very-long-project-stem", "digits-123"):
        generated = sandbox_name(Path(f"/code/{stem}"))
        assert validate_sandbox_name(generated) == generated


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
    diff is where that fact becomes visible.

    Inference egress is granted by the *provider*, not this policy: the gateway synthesizes a
    `_provider_claude_code` rule when the provider attaches, and a second rule for the same
    endpoints is a hard create-time failure (ambiguity validation), so its absence here is
    itself a security property to pin."""
    with patch.object(openshell, "import_protos", _fake_protos):
        policy = openshell.build_default_policy()

    assert policy.version == 1
    rules = policy.network_policies
    assert set(rules) == {"python_packages"}

    pypi = rules["python_packages"]
    assert {e["host"] for e in pypi.endpoints} == {"pypi.org", "files.pythonhosted.org"}
    assert all(e["port"] == 443 for e in pypi.endpoints)
    assert {b["path"] for b in pypi.binaries} == {
        "/usr/local/bin/pip", "/usr/bin/pip", "/usr/local/bin/uv", "/usr/bin/uv",
    }

    # No inference endpoints of our own: the attached provider is the only route to them,
    # and the binary restriction on that route comes from the provider profile.
    for rule in rules.values():
        assert all(
            e["host"] not in {"api.anthropic.com", "statsig.anthropic.com", "sentry.io"}
            for e in rule.endpoints
        )

    # The workspace is writable; the process runs as a stated non-root identity, because an
    # omitted one falls back to the image's OCI USER whose primary GID is 0 — and the gateway
    # hard-rejects any workload identity containing GID 0. The image's `o=u` mode recipe keeps
    # its writable paths open to this gid, so the other targets are unaffected.
    assert policy.filesystem == {"include_workdir": True, "read_only": ["/opt/factory"]}
    assert policy.process == {"run_as_user": "1001", "run_as_group": "1001"}


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
    assert plan.labels["factory.project"]
    # A source path is not a legal label value on a gateway (alphanumeric/-/_/. only), so it is
    # deliberately absent — `workspace_for` recovers it from the local workspace copy instead.
    assert "factory.source" not in plan.labels
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
# Run model: detached launch, log/pid/exit artifacts, liveness
# --------------------------------------------------------------------------------------------


def test_the_run_launch_detaches_logs_and_records_its_own_lifecycle() -> None:
    """The launch script is the target's `build_tmux_launch` replacement, so its properties are
    the contract: detached (nohup + background), output to the run log, pid to the pidfile, exit
    code to the exit file — and *no tmux anywhere*, because a sandbox cannot allocate a PTY.
    This test also executes the script, because a composed-but-unparsed shell string is how the
    `&`-vs-`cd` precedence bug shipped: the pidfile landed in the launcher's cwd, not the run's.
    """
    import subprocess as sp

    from factory.contained.openshell import build_run_launch

    script = build_run_launch("/workspace/rta", "factory study /workspace/rta")
    assert "nohup" in script and "&" in script          # detached: exec returns immediately
    for token in (".factory/run.log", ".factory/run.pid", ".factory/run.exit"):
        assert token in script, f"the run's {token} artifact is missing from the launch"
    assert "tmux" not in script                          # cannot work in a sandbox (#749)
    assert sp.run(["sh", "-n"], input=script, text=True).returncode == 0

    # Execute it for real: the three artifacts must exist, in the *run's* directory.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        run = build_run_launch(str(project), "echo hi-from-run; sleep 0.1")
        sp.run(["sh", "-c", run], check=True, cwd=tmp, capture_output=True)
        deadline = time.monotonic() + 5
        while not (project / ".factory/run.exit").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        artifacts = sorted(p.name for p in (project / ".factory").iterdir())
        assert artifacts == ["run.exit", "run.log", "run.pid"]
        assert (project / ".factory/run.log").read_text().strip() == "hi-from-run"
        assert (project / ".factory/run.exit").read_text().strip() == "0"


def test_run_liveness_reads_the_exit_file_before_the_pid() -> None:
    """Liveness precedence: the exit file is authoritative (a recycled pid after a sandbox
    stop/start must not resurrect a finished run), a live pidfile means running, and neither
    file means no run was started."""
    from factory.contained.openshell import run_liveness_probe

    @dataclass
    class _Result:
        exit_code: int
        stdout: str

    class _Session:
        def __init__(self, stdout: str):
            self._stdout = stdout

        def exec(self, argv, **kwargs):
            # The probe's shell decides; the fake answers what the shell would print.
            assert argv[0] == "sh"
            return _Result(exit_code=0, stdout=self._stdout)

    assert run_liveness_probe(_Session("finished\n")) == "finished"
    assert run_liveness_probe(_Session("running\n")) == "running"
    assert run_liveness_probe(_Session("")) == "unknown"          # probe broke: say so
    assert run_liveness_probe(_Session("garbage\n")) == "unknown"


def test_the_attach_argv_follows_the_log_and_never_touches_tmux() -> None:
    """Attach is a read-only log follow through the CLI's outer PTY (which the supervisor
    allocates *before* the Landlock boundary, so it works where a nested one cannot)."""
    from factory.contained.openshell import build_attach_argv

    argv = build_attach_argv("rta-abc123")
    assert argv[:6] == ["openshell", "sandbox", "exec", "-n", "rta-abc123", "--tty"]
    script = argv[-1]
    assert "tail -n 200 -f .factory/run.log" in script
    assert "run.exit" in script                            # the finished run's stamp
    assert "tmux" not in script


def test_the_run_environment_is_unbuffered() -> None:
    """The run's stdout is a file, where Python block-buffers; unbuffered output is what keeps
    `attach`'s log tail distinguishable from a hang."""
    from factory.cli.contained_openshell import _build_plan

    project = tmp_project()
    args = _plan_args(project)
    plan = _build_plan(args, _workspace(project), "rta-abc123", {}, {})
    assert plan.env["PYTHONUNBUFFERED"] == "1"
    # setdefault semantics: an explicit --env PYTHONUNBUFFERED=0 is the user's to make
    plan2 = _build_plan(
        _plan_args(project, "--env", "PYTHONUNBUFFERED=0"), _workspace(project), "rta-abc123",
        {}, {"PYTHONUNBUFFERED": "0"},
    )
    assert plan2.env["PYTHONUNBUFFERED"] == "0"


def tmp_project() -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp()) / "rta"
    path.mkdir()
    return path


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

