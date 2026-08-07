"""The cluster launch sequence: materialize, scan, pack, provision, assert, start.

The ordering is the safety property. The secret scan gates the upload, and the provenance probes
gate the first agent call — a run that reaches the factory with a filtered workspace has already
spent the upload. So the assertions here are mostly about *when* a step runs relative to the others,
not only that it runs.

Nothing here may touch a cluster. Every `oc`/`kubectl` seam is patched at the name
`factory.cli.contained_k8s` imported it under, plus `subprocess.run` inside the module for the two
places it shells out directly.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.cli import contained as cli
from factory.cli import contained_k8s
from factory.cli.contained_k8s import (
    PACK_EXCLUDES,
    _build_pod_plan,
    _pack,
    _provision,
    _require_openshift,
    _scan_and_confirm,
    _start,
    run_k8s,
)
from factory.contained.k8s import ClusterError, PodPlan
from factory.contained.secrets import Finding, ScanResult
from factory.contained.workspace import Workspace, plan_workspace


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture(autouse=True)
def _no_cluster() -> None:
    """No test in this file is allowed to reach a cluster or a real kubeconfig.

    `_build_pod_plan` reads the namespace's allocated fsGroup range and the credential Secret's key
    names; both are live `oc get` calls on a machine that is logged in. On a slow or unreachable
    cluster that is a 30-second timeout per test.
    """
    with patch("factory.cli.contained_k8s.namespace_fs_group", return_value=None), \
         patch("factory.cli.contained_k8s.secret_keys", return_value=set()), \
         patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("")):
        yield  # type: ignore[misc]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    path = tmp_path / "rta"
    path.mkdir()
    (path / "README.md").write_text("# rta\n")
    return path


@pytest.fixture()
def contained_root(tmp_path: Path):
    root = tmp_path / "contained-home"
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(root)}, clear=False):
        yield root


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(["contained", *argv])
    cli.interpret(cli._PARSER, args)
    return args


def _workspace(project: Path, contained_root: Path) -> Workspace:
    """The workspace `materialize` would have produced, with the copy actually on disk."""
    ws = plan_workspace(project, "rta-abc123", self_contained=True)
    ws.path.mkdir(parents=True, exist_ok=True)
    (ws.path / "README.md").write_text("# rta\n")
    return ws


def _plan(project: Path, contained_root: Path, **overrides: object) -> PodPlan:
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    for key, value in overrides.items():
        setattr(args, key, value)
    return _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")


# --------------------------------------------------------------------------------------------
# The plan: what crosses into the pod manifest, and what is only warned about
# --------------------------------------------------------------------------------------------


def test_forwarding_a_variable_that_is_not_set_fails_before_anything_is_uploaded(
    project: Path, contained_root: Path
) -> None:
    """`--forward` names a variable the user believes is exported. Discovering it is not, after a
    workspace has crossed the network, wastes the upload and reads as a cluster fault."""
    from factory.contained.errors import ContainedError

    args = _args([
        "--target", "k8s", "--namespace", "ns", "--forward", "NOT_SET_ANYWHERE",
        "--", "ceo", str(project),
    ])
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOT_SET_ANYWHERE", None)
        with pytest.raises(ContainedError, match="NOT_SET_ANYWHERE"):
            _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")


def test_a_forwarded_variable_reaches_the_pod_environment(
    project: Path, contained_root: Path
) -> None:
    args = _args([
        "--target", "k8s", "--namespace", "ns", "--forward", "FORWARDED_MARKER",
        "--", "ceo", str(project),
    ])
    with patch.dict(os.environ, {"FORWARDED_MARKER": "yes"}, clear=False):
        plan = _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")
    assert plan.env["FORWARDED_MARKER"] == "yes"


def test_a_credential_looking_variable_warns_that_the_manifest_is_readable(
    project: Path, contained_root: Path
) -> None:
    """Pod env lands in the manifest, visible to anyone who can read pods in the namespace. The
    Secret is the supported route, so forwarding a key is a warning rather than a silent success."""
    args = _args([
        "--target", "k8s", "--namespace", "ns", "--env", "SOME_API_KEY=sk-live-1234",
        "--", "ceo", str(project),
    ])
    plan = _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")
    assert any("visible to anyone who can read pods" in w for w in plan.warnings)
    assert "sk-live-1234" not in " ".join(plan.warnings)


def test_a_google_credential_in_the_secret_becomes_a_file_path_not_a_value(
    project: Path, contained_root: Path
) -> None:
    """ADC has to arrive as a *file*, so the launch has to know one is there — by key name only.
    The value never leaves the cluster."""
    from factory.contained.k8s import ADC_PATH, ADC_SECRET_KEY

    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.secret_keys", return_value={ADC_SECRET_KEY}):
        plan = _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")
    assert plan.adc is True
    assert plan.env["GOOGLE_APPLICATION_CREDENTIALS"] == ADC_PATH


def test_a_vertex_payload_without_an_explicit_model_carries_the_quota_warning(
    project: Path, contained_root: Path
) -> None:
    """A model whose per-minute quota is zero 429s every call, which reads as a network fault."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch.dict(os.environ, {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": "us-east5",
        "ANTHROPIC_VERTEX_PROJECT_ID": "p",
    }, clear=False):
        plan = _build_pod_plan(args, _workspace(project, contained_root), "ns", "rta-abc123")
    assert any("--model" in w for w in plan.warnings)


def test_the_payloads_project_path_is_rewritten_to_the_pods_workspace(
    project: Path, contained_root: Path
) -> None:
    """Unlike the local target this is not path-preserving — nothing outside the pod resolves it."""
    plan = _plan(project, contained_root)
    assert plan.project_dir.endswith("/rta")
    assert str(project) not in plan.factory_command
    assert plan.project_dir in plan.factory_command


# --------------------------------------------------------------------------------------------
# The division refuses at launch when the cluster cannot serve it
# --------------------------------------------------------------------------------------------


def test_the_division_is_refused_on_a_cluster_without_the_build_api() -> None:
    """A run that gets as far as submitting a Build the cluster will never admit has already spent
    a workspace upload and a pod start."""
    with patch("factory.contained.k8s_division.openshift_available", return_value=False):
        with pytest.raises(ClusterError, match="build.openshift.io"):
            _require_openshift(dry_run=False)


def test_the_division_is_allowed_on_a_cluster_that_serves_builds() -> None:
    with patch("factory.contained.k8s_division.openshift_available", return_value=True):
        _require_openshift(dry_run=False)


def test_a_division_run_is_refused_before_the_workspace_is_materialized(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal is worth nothing if it lands after the copy — that is the expensive step."""
    args = _args([
        "--target", "k8s", "--namespace", "ns", "--division", "--", "ceo", str(project),
    ])
    with patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_division.openshift_available", return_value=False), \
         patch("factory.cli.contained_k8s.materialize") as materialize:
        assert run_k8s(args) == 2
    materialize.assert_not_called()
    assert "build.openshift.io" in capsys.readouterr().err


def test_dry_run_does_not_ask_the_cluster_whether_it_serves_builds() -> None:
    """Composing a command must not require a reachable cluster."""
    with patch("factory.contained.k8s_division.openshift_available") as probe:
        _require_openshift(dry_run=True)
    probe.assert_not_called()


# --------------------------------------------------------------------------------------------
# The secret scan gates the upload
# --------------------------------------------------------------------------------------------


def test_findings_block_the_upload_when_nobody_can_answer(
    project: Path, contained_root: Path
) -> None:
    """Non-interactive with findings and no `--yes` must refuse, not hang and not proceed."""
    ws = _workspace(project, contained_root)
    result = ScanResult(scanned=True, findings=(Finding(".env", 1, "generic", "key"),), detail="1")
    with patch("factory.cli.contained_k8s.scan", return_value=result), \
         patch("sys.stdin.isatty", return_value=False):
        assert _scan_and_confirm(ws, assume_yes=False) is False


def test_yes_overrides_findings_and_is_recorded(project: Path, contained_root: Path) -> None:
    ws = _workspace(project, contained_root)
    result = ScanResult(scanned=True, findings=(Finding(".env", 1, "generic", "key"),), detail="1")
    with patch("factory.cli.contained_k8s.scan", return_value=result):
        assert _scan_and_confirm(ws, assume_yes=True) is True


def test_a_refused_scan_stops_the_run_before_the_pod_exists(
    project: Path, contained_root: Path
) -> None:
    """The whole point of scanning is that nothing leaves the machine first — so a refusal must
    happen before the PVC and the pod are applied, not after."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.materialize",
               return_value=_workspace(project, contained_root)), \
         patch("factory.cli.contained_k8s._scan_and_confirm", return_value=False), \
         patch("factory.cli.contained_k8s._pack") as pack, \
         patch("factory.cli.contained_k8s.apply_manifest") as apply:
        assert run_k8s(args) == 1
    pack.assert_not_called()
    apply.assert_not_called()


# --------------------------------------------------------------------------------------------
# Packing
# --------------------------------------------------------------------------------------------


def test_the_tarball_unpacks_under_the_projects_own_name(
    project: Path, contained_root: Path
) -> None:
    """Packed as `<project>/...` rather than `./...` so it lands at `/workspace/<project>`, the
    path the working directory, the rewritten payload and the probes already agree on."""
    ws = _workspace(project, contained_root)
    tarball = _pack(ws, "rta-abc123")
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert all(name == "rta" or name.startswith("rta/") for name in names)


def test_host_shaped_directories_are_never_packed(project: Path, contained_root: Path) -> None:
    """An arm64 .venv unpacked onto an amd64 node is actively wrong, not merely wasteful."""
    ws = _workspace(project, contained_root)
    (ws.path / ".venv" / "lib").mkdir(parents=True)
    (ws.path / ".venv" / "lib" / "x.so").write_text("binary")
    tarball = _pack(ws, "rta-abc123")
    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert not any(".venv" in name for name in names)
    assert "rta/README.md" in names


def test_git_is_packed_because_the_pod_has_no_host_to_point_at(
    project: Path, contained_root: Path
) -> None:
    """Without `.git` the pod reports no_repo, the CEO silently drops to build mode, and the
    eventual error names a flag several steps from the cause."""
    assert ".git" not in PACK_EXCLUDES
    ws = _workspace(project, contained_root)
    (ws.path / ".git").mkdir()
    (ws.path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    tarball = _pack(ws, "rta-abc123")
    with tarfile.open(tarball) as archive:
        assert "rta/.git/HEAD" in archive.getnames()


# --------------------------------------------------------------------------------------------
# Provisioning: the identifier is printed before any long-running work
# --------------------------------------------------------------------------------------------


def test_the_run_identifier_is_printed_before_the_upload_blocks(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run whose name the user cannot see is a run they cannot manage — and the upload is the
    long step."""
    plan = _plan(project, contained_root)
    order: list[str] = []
    with patch("factory.cli.contained_k8s.apply_manifest"), \
         patch("factory.cli.contained_k8s.wait_for_container", return_value="running"), \
         patch("factory.cli.contained_k8s.stream_workspace",
               side_effect=lambda *a: order.append("upload")):
        _provision(plan, Path("/tmp/upload.tar.gz"))
    printed = capsys.readouterr().out
    assert plan.name in printed
    assert order == ["upload"]


def test_a_loader_that_already_finished_does_not_re_upload(
    project: Path, contained_root: Path
) -> None:
    """The unpack marker is per-run, so a terminated loader means this run's files are already
    there — a pod restart after a successful upload, never a previous run's stale tree."""
    plan = _plan(project, contained_root)
    with patch("factory.cli.contained_k8s.apply_manifest"), \
         patch("factory.cli.contained_k8s.wait_for_container", return_value="terminated"), \
         patch("factory.cli.contained_k8s.stream_workspace") as upload:
        _provision(plan, Path("/tmp/upload.tar.gz"))
    upload.assert_not_called()


def test_the_claim_is_applied_before_the_pod_that_mounts_it(
    project: Path, contained_root: Path
) -> None:
    plan = _plan(project, contained_root)
    applied: list[str] = []
    with patch("factory.cli.contained_k8s.apply_manifest",
               side_effect=lambda manifest, ns: applied.append(manifest.split("kind: ")[1][:30])), \
         patch("factory.cli.contained_k8s.wait_for_container", return_value="running"), \
         patch("factory.cli.contained_k8s.stream_workspace"):
        _provision(plan, Path("/tmp/upload.tar.gz"))
    assert applied[0].startswith("PersistentVolumeClaim")
    assert applied[1].startswith("Pod")


# --------------------------------------------------------------------------------------------
# Starting: provenance first, then the collision check, then tmux
# --------------------------------------------------------------------------------------------


def test_a_failed_provenance_assertion_stops_before_the_factory_starts(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The packer copies what it is told, so the filtered-transfer trap a bind mount removed
    locally is live here — and it has to be caught before the first agent call spends tokens."""
    plan = _plan(project, contained_root)
    ws = _workspace(project, contained_root)
    with patch("factory.cli.contained_k8s.subprocess.run",
               return_value=_completed("", returncode=1)) as run:
        assert _start(plan, ws, project) == 1
    err = capsys.readouterr().err
    assert "assertion" in err
    # The pod is deliberately left up, and the message says how to look inside it.
    assert f"oc exec -it {plan.name}" in err
    # One failing probe is enough; nothing else is attempted.
    assert run.call_count == 1


def test_a_pod_already_running_a_session_is_named_as_the_same_run(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`apply` is idempotent, so a re-invocation reuses the pod and the tmux launch collides. Raw,
    that surfaces as "duplicate session: factory", which names tmux for "you already have this
    run"."""
    plan = _plan(project, contained_root)
    ws = _workspace(project, contained_root)
    with patch("factory.cli.contained_k8s.subprocess.run", return_value=_completed()):
        assert _start(plan, ws, project) == 1
    err = capsys.readouterr().err
    assert "already running a session" in err
    assert "tmux" not in err


def test_a_successful_start_prints_attach_sync_and_logs(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan(project, contained_root)
    ws = _workspace(project, contained_root)
    calls: list[list[str]] = []

    def _fake(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        # Probes succeed; `tmux has-session` must report "no session" so the launch proceeds.
        return _completed("", returncode=1 if "has-session" in argv else 0)

    with patch("factory.cli.contained_k8s.subprocess.run", side_effect=_fake):
        assert _start(plan, ws, project) == 0
    out = capsys.readouterr().out
    assert "attach:" in out and "result:" in out and "logs:" in out
    assert any("new-session" in " ".join(argv) for argv in calls)


def test_a_launch_that_fails_reports_the_clusters_own_error(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan(project, contained_root)
    ws = _workspace(project, contained_root)

    def _fake(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "has-session" in argv:
            return _completed("", returncode=1)
        if "new-session" in " ".join(argv):
            return _completed("", returncode=1, stderr="no tmux in this image")
        return _completed()

    with patch("factory.cli.contained_k8s.subprocess.run", side_effect=_fake):
        assert _start(plan, ws, project) == 1
    assert "no tmux in this image" in capsys.readouterr().err


# --------------------------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------------------------


def test_dry_run_prints_the_manifests_and_provisions_nothing(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.dry_run_enabled", return_value=True), \
         patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.materialize") as materialize, \
         patch("factory.cli.contained_k8s.apply_manifest") as apply, \
         patch("factory.cli.contained_k8s.subprocess.run", wraps=subprocess.run) as run:
        assert run_k8s(args) == 0
    materialize.assert_not_called()
    apply.assert_not_called()
    # A read-only `git rev-parse` is the one filesystem interaction dry-run keeps — it decides
    # worktree vs. copy and changes nothing. Nothing may reach a cluster or a container engine.
    assert not [c for c in run.call_args_list if c.args[0][0] in ("oc", "kubectl", "podman")]
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "kind: PersistentVolumeClaim" in out
    assert "kind: Pod" in out
    assert "[upload]" in out and "[run]" in out


def test_dry_run_creates_no_workspace_on_disk(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`plan_workspace` rather than `materialize`: composing a command must not rsync a tree."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.dry_run_enabled", return_value=True), \
         patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"):
        assert run_k8s(args) == 0
    assert not contained_root.exists()


def test_an_unresolvable_namespace_is_reported_not_raised(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`resolve_namespace` raises `ClusterError`, a `ContainedError`; the CLI turns that into an
    exit code and a message rather than a traceback."""
    args = _args(["--target", "k8s", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.resolve_namespace",
               side_effect=ClusterError("no namespace given")):
        assert run_k8s(args) == 2
    assert "no namespace given" in capsys.readouterr().err


def test_a_payload_naming_no_project_is_rejected_before_a_namespace_is_resolved(
    capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "backlog-list"])
    with patch("factory.cli.contained_k8s.resolve_namespace") as resolve:
        assert run_k8s(args) == 2
    resolve.assert_not_called()
    assert "no existing directory" in capsys.readouterr().err


def test_a_cluster_error_during_provisioning_exits_one_not_two(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 means "you asked for something impossible"; 1 means "the cluster said no". A wrapper
    that retries on 1 and gives up on 2 depends on the difference."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--yes", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.materialize",
               return_value=_workspace(project, contained_root)), \
         patch("factory.cli.contained_k8s._scan_and_confirm", return_value=True), \
         patch("factory.cli.contained_k8s.apply_manifest",
               side_effect=ClusterError("forbidden: cannot create pods")):
        assert run_k8s(args) == 1
    assert "forbidden" in capsys.readouterr().err


def test_a_successful_launch_records_that_this_machine_uses_the_cluster(
    project: Path, contained_root: Path
) -> None:
    """`ls` only reaches for a cluster the user has actually used; the launch is what records it."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--yes", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.materialize",
               return_value=_workspace(project, contained_root)), \
         patch("factory.cli.contained_k8s._scan_and_confirm", return_value=True), \
         patch("factory.cli.contained_k8s._pack", return_value=Path("/tmp/x.tar.gz")), \
         patch("factory.cli.contained_k8s._provision"), \
         patch("factory.cli.contained_k8s._start", return_value=0):
        assert run_k8s(args) == 0
    from factory.contained.usage import uses

    assert uses("k8s")


def test_the_growth_context_warning_reaches_the_cluster_path(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scores computed in a pod without this context are not comparable to host scores, and the
    operator needs to know that before comparing them."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.dry_run_enabled", return_value=True), \
         patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.growth_context_warning", return_value="scores differ"):
        assert run_k8s(args) == 0
    assert "Warning: scores differ" in capsys.readouterr().err


def test_an_absent_growth_warning_does_not_swallow_the_plans_own_warnings(
    project: Path, contained_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two sources are concatenated, so a `None` in the middle must be skipped rather than
    ending the list — that would drop every warning the plan itself raised."""
    args = _args(["--target", "k8s", "--namespace", "ns", "--", "ceo", str(project)])
    with patch("factory.cli.contained_k8s.dry_run_enabled", return_value=True), \
         patch("factory.cli.contained_k8s.resolve_namespace", return_value="ns"), \
         patch("factory.cli.contained_k8s.growth_context_warning", return_value=None), \
         patch.dict(os.environ, {
             "CLAUDE_CODE_USE_VERTEX": "1",
             "CLOUD_ML_REGION": "us-east5",
             "ANTHROPIC_VERTEX_PROJECT_ID": "p",
         }, clear=False):
        assert run_k8s(args) == 0
    assert "--model" in capsys.readouterr().err


def test_the_module_uses_the_same_tmux_launch_as_the_local_target(
    project: Path, contained_root: Path
) -> None:
    """One composer for both targets: a session created differently in a pod is a session `attach`
    cannot find."""
    from factory.podman import build_tmux_launch

    plan = _plan(project, contained_root)
    assert contained_k8s._tmux_launch(plan) == build_tmux_launch(
        plan.project_dir, plan.run_command
    )
