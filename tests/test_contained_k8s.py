"""The cluster runtime and the cluster division: manifests, transport, RBAC, and the boundary."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from factory.cli import contained as cli
from factory.cli.contained_k8s import PACK_EXCLUDES, _build_pod_plan, _pack
from factory.contained import k8s, k8s_setup, secrets
from factory.contained.bundle import SCC_ROLEBINDING, render_bundle
from factory.contained.k8s import (
    FACTORY_CONTAINER,
    LABEL_CONTAINED,
    LOADER_CONTAINER,
    PVC_NAME,
    SERVICE_ACCOUNT,
    WORKSPACE_ROOT,
    PodPlan,
    render_access_review,
    build_pod_attach_argv,
    loader_command,
    render_pod,
    render_pvc,
    unpack_command,
)
from factory.contained.prereq import Check
from factory.contained.workspace import plan_workspace


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(["contained", *argv])
    cli.interpret(cli._PARSER, args)
    return args


def _plan(tmp_path: Path, *, division: bool = False) -> PodPlan:
    project = tmp_path / "rta"
    project.mkdir(exist_ok=True)
    args = _args(
        ["--target", "k8s", "--namespace", "ns", *(["--division"] if division else []),
         "--", "ceo", str(project)]
    )
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(tmp_path / "home")}, clear=False):
        ws = plan_workspace(project, "rta-test")
        return _build_pod_plan(args, ws, "ns", "rta-test")


# --------------------------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------------------------


def test_the_bundle_is_valid_yaml_and_namespace_scoped() -> None:
    docs = [d for d in yaml.safe_load_all(render_bundle(namespace="ns")) if d]
    kinds = {d["kind"] for d in docs}
    assert kinds == {"ServiceAccount", "Role", "RoleBinding", "PersistentVolumeClaim"}
    for doc in docs:
        assert doc["metadata"]["namespace"] == "ns", f"{doc['kind']} is not namespace-scoped"
    # Binding to a pre-existing cluster SCC is allowed; creating one is not.
    assert "ClusterRole" not in kinds
    assert "SecurityContextConstraints" not in kinds


def test_the_bundle_never_grants_pods_exec() -> None:
    """The build sidecar is a boundary only because the agent cannot exec into it."""
    for division in (False, True):
        docs = [d for d in yaml.safe_load_all(render_bundle(namespace="ns", division=division)) if d]
        role = next(d for d in docs if d["kind"] == "Role")
        resources = {r for rule in role["rules"] for r in rule["resources"]}
        assert "pods/exec" not in resources
        assert not any("exec" in r for r in resources)


def test_the_division_adds_build_verbs_and_nothing_else() -> None:
    plain = next(d for d in yaml.safe_load_all(render_bundle(namespace="ns")) if d
                 and d["kind"] == "Role")
    with_division = next(d for d in yaml.safe_load_all(render_bundle(namespace="ns", division=True))
                         if d and d["kind"] == "Role")
    plain_groups = {rule.get("apiGroups", [""])[0] for rule in plain["rules"]}
    division_groups = {rule.get("apiGroups", [""])[0] for rule in with_division["rules"]}
    assert plain_groups == {""}
    assert division_groups == {"", "build.openshift.io", "image.openshift.io"}


def test_the_bundle_carries_the_secret_command_but_never_the_secret() -> None:
    """The factory references the Secret by name and never handles the material."""
    text = render_bundle(namespace="ns")
    assert "oc create secret generic factory-credentials" in text
    docs = [d for d in yaml.safe_load_all(text) if d]
    assert not any(d["kind"] == "Secret" for d in docs)


def test_the_bundle_renders_with_no_cluster_reachable() -> None:
    """An explicit namespace is all it needs — the cluster does not have to be up to print YAML."""
    with patch("factory.contained.k8s.current_namespace", side_effect=k8s.ClusterError("no cli")):
        assert "kind: ServiceAccount" in render_bundle(namespace="ns")


def test_the_bundle_never_invents_a_namespace() -> None:
    """Cluster YAML pinned to a guessed name invites the user to apply it somewhere they did not
    intend, and "it defaulted to `factory`" is not something they would think to check."""
    from factory.contained.errors import ContainedError

    with patch("factory.contained.k8s.current_namespace", return_value=None):
        with pytest.raises(ContainedError, match="--namespace"):
            render_bundle()


def test_the_command_the_bundle_prints_is_one_the_cli_accepts() -> None:
    """The generated header is copy-pasted; a flag after the subcommand is rejected by the parser."""
    text = render_bundle(namespace="ns")
    assert "factory contained --namespace ns bundle |" in text
    assert "contained bundle --namespace" not in text


# --------------------------------------------------------------------------------------------
# The pod
# --------------------------------------------------------------------------------------------


def test_the_pod_is_restricted_scc_compatible(tmp_path: Path) -> None:
    doc = yaml.safe_load(render_pod(_plan(tmp_path)))
    assert doc["spec"]["securityContext"]["runAsNonRoot"] is True
    assert doc["spec"]["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    # No UID is pinned: the namespace picks one and the image is built for arbitrary UIDs.
    assert "runAsUser" not in doc["spec"]["securityContext"]
    for container in doc["spec"]["containers"] + doc["spec"]["initContainers"]:
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert "privileged" not in container["securityContext"]


def test_the_pod_carries_no_host_mounts(tmp_path: Path) -> None:
    doc = yaml.safe_load(render_pod(_plan(tmp_path)))
    for volume in doc["spec"]["volumes"]:
        assert "hostPath" not in volume
    assert doc["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == PVC_NAME


def test_credentials_come_from_the_namespace_secret(tmp_path: Path) -> None:
    doc = yaml.safe_load(render_pod(_plan(tmp_path)))
    factory = next(c for c in doc["spec"]["containers"] if c["name"] == FACTORY_CONTAINER)
    assert factory["envFrom"][0]["secretRef"]["name"] == "factory-credentials"
    # `optional: false` — a missing Secret fails the pod at start rather than inside an agent call.
    assert factory["envFrom"][0]["secretRef"]["optional"] is False


def test_the_pvc_is_rwo_and_survives_the_pod() -> None:
    doc = yaml.safe_load(render_pvc("ns", None))
    assert doc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert doc["metadata"]["name"] == PVC_NAME
    assert "storageClassName" not in doc["spec"]          # cluster default unless asked
    assert yaml.safe_load(render_pvc("ns", "gp3"))["spec"]["storageClassName"] == "gp3"


def test_the_loader_waits_for_the_upload_and_gives_up_eventually(tmp_path: Path) -> None:
    """A host that died mid-upload must not pin a pod in Init forever."""
    command = loader_command("rta-test")
    assert k8s.unpack_marker("rta-test") in command
    assert str(k8s.LOADER_TIMEOUT_SECONDS) in command
    doc = yaml.safe_load(render_pod(_plan(tmp_path)))
    loader = next(c for c in doc["spec"]["initContainers"] if c["name"] == LOADER_CONTAINER)
    assert loader["volumeMounts"][0]["mountPath"] == WORKSPACE_ROOT


def test_the_marker_is_per_run_so_a_reused_pvc_cannot_serve_stale_files() -> None:
    """The PVC outlives the run that filled it. A shared marker means the *next* run finds it
    present, skips its own upload, and quietly runs against the previous run's files."""
    assert k8s.unpack_marker("run-a") != k8s.unpack_marker("run-b")
    assert "run-a" in loader_command("run-a")
    assert "run-a" in unpack_command("run-a")


def test_the_marker_is_written_only_on_a_successful_unpack() -> None:
    """A partial transfer must leave the loader waiting, not start the factory on half a tree."""
    command = unpack_command("rta-test")
    assert command.index("tar xzf") < command.index("&&") < command.index("touch")


def test_the_workspace_is_packed_once_not_copied_file_by_file(tmp_path: Path) -> None:
    import tarfile

    project = tmp_path / "rta"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.go").write_text("package main\n")
    (project / ".venv").mkdir()
    (project / ".venv" / "huge").write_text("x" * 1000)
    (project / ".factory").mkdir()
    (project / ".factory" / "config.json").write_text("{}")

    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(tmp_path / "home")}, clear=False):
        ws = plan_workspace(project, "rta-test")
        # plan_workspace does not copy, so point the pack at the project itself.
        ws = type(ws)(source=project, path=project, kind="copy")
        tarball = _pack(ws, "rta-test")

    with tarfile.open(tarball) as archive:
        names = archive.getnames()
    assert "rta/src/main.go" in names
    # .factory/ must survive — it is gitignored by convention and holds the whole history.
    assert "rta/.factory/config.json" in names
    # Host-shaped directories must not: an arm64 .venv on an amd64 node is actively wrong.
    assert not any(name.startswith("rta/.venv") for name in names)
    assert ".venv" in PACK_EXCLUDES


def test_the_project_lands_where_the_payload_was_rewritten_to(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.project_dir == f"{WORKSPACE_ROOT}/rta"
    assert plan.project_dir in plan.factory_command


# --------------------------------------------------------------------------------------------
# Lifecycle over factory-created pods only
# --------------------------------------------------------------------------------------------


def test_pods_are_selected_by_the_factory_label() -> None:
    argv = k8s.build_get_pods_argv("ns")
    assert "-l" in argv
    assert f"{LABEL_CONTAINED}=true" in argv


def test_attach_is_oc_exec_into_tmux() -> None:
    argv = build_pod_attach_argv("rta-test", "ns")
    assert argv[1] == "exec"
    assert "-t" in argv
    assert argv[-4:] == ["tmux", "attach", "-t", "factory"]


def test_cluster_runtimes_reports_pods_as_runtimes() -> None:
    payload = {
        "items": [
            {
                "metadata": {
                    "name": "rta-test",
                    "labels": {"factory.contained": "true", "factory.project": "deadbeef"},
                    "creationTimestamp": "2026-08-04T00:00:00Z",
                },
                "status": {"phase": "Running"},
            }
        ]
    }
    with patch("factory.contained.k8s._run", return_value=_completed(json.dumps(payload))):
        runtimes = k8s.cluster_runtimes("ns")
    assert [(r.name, r.target, r.state) for r in runtimes] == [("rta-test", "k8s", "Running")]


def test_rm_leaves_the_pvc_alone(capsys: pytest.CaptureFixture[str]) -> None:
    """The PVC holds the only copy of a multi-hour run's work."""
    with patch("factory.contained.k8s._run", return_value=_completed()) as run:
        code = k8s.remove_cluster_runtime("rta-test", namespace="ns")
    assert code == 0
    deletes = [call.args[0] for call in run.call_args_list]
    assert not any("pvc" in " ".join(argv) for argv in deletes)
    assert PVC_NAME in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# The secret scan
# --------------------------------------------------------------------------------------------


def test_a_missing_scanner_warns_and_proceeds(capsys: pytest.CaptureFixture[str]) -> None:
    """Refusing to run without an optional tool would make it mandatory by the back door."""
    result = secrets.ScanResult(scanned=False, detail="gitleaks is not installed")
    assert secrets.confirm_upload(result, assume_yes=False, interactive=False) is True
    assert "not installed" in capsys.readouterr().err


def test_a_clean_scan_asks_nothing() -> None:
    result = secrets.ScanResult(scanned=True, findings=(), detail="no secrets found")
    assert secrets.confirm_upload(result, assume_yes=False, interactive=False) is True


def test_findings_block_a_non_interactive_upload(capsys: pytest.CaptureFixture[str]) -> None:
    result = secrets.ScanResult(
        scanned=True,
        findings=(secrets.Finding(file=".env", line=1, rule="aws-key", description="AWS key"),),
        detail="1 finding(s)",
    )
    assert secrets.confirm_upload(result, assume_yes=False, interactive=False) is False
    err = capsys.readouterr().err
    assert ".env:1" in err
    assert "cluster storage" in err


def test_yes_overrides_and_is_recorded(capsys: pytest.CaptureFixture[str]) -> None:
    """A warn-and-confirm gate, not a hard block — but the override is never silent."""
    result = secrets.ScanResult(
        scanned=True,
        findings=(secrets.Finding(file=".env", line=1, rule="aws-key", description="AWS key"),),
        detail="1 finding(s)",
    )
    assert secrets.confirm_upload(result, assume_yes=True, interactive=False) is True
    assert "--yes was given" in capsys.readouterr().err


def test_the_scan_reads_the_tree_not_the_history() -> None:
    argv = secrets.build_scan_argv(Path("/w"), Path("/tmp/r.json"))
    assert argv[1] == "dir"
    assert "/w" in argv


def test_a_scan_never_raises_when_gitleaks_is_absent(tmp_path: Path) -> None:
    with patch("factory.contained.secrets.shutil.which", return_value=None):
        result = secrets.scan(tmp_path)
    assert result.scanned is False
    assert "UNSCANNED" in result.detail


# --------------------------------------------------------------------------------------------
# verify — every failure carries its fix
# --------------------------------------------------------------------------------------------


def test_no_cli_reports_one_failure_not_nine() -> None:
    with patch("factory.contained.k8s_setup.cli_binary",
               side_effect=k8s.ClusterError("neither oc nor kubectl")):
        checks = k8s_setup.verify_k8s(namespace="ns")
    assert len(checks) == 1
    assert not checks[0].ok
    assert checks[0].fix


def test_no_context_stops_before_reporting_eight_more_failures() -> None:
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed(returncode=1)):
        checks = k8s_setup.verify_k8s(namespace="ns")
    assert len(checks) == 1
    assert checks[0].name == "cluster_cli"
    assert "login" in (checks[0].fix or "")


def test_a_missing_object_names_the_command_that_restores_it() -> None:
    def fake_run(argv, **kwargs):
        if "current-context" in argv:
            return _completed("ctx")
        if "rolebinding" in argv and SCC_ROLEBINDING in argv:
            return _completed(returncode=1)
        return _completed("ok")

    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", side_effect=fake_run):
        checks = k8s_setup.verify_k8s(namespace="ns")
    missing = [c for c in checks if not c.ok and SCC_ROLEBINDING in c.name]
    assert missing
    # Flag before subcommand — the form the parser actually accepts.
    assert "factory contained --namespace ns" in (missing[0].fix or "")
    assert "bundle |" in (missing[0].fix or "")


def test_permissions_are_checked_as_the_service_account_not_as_the_user() -> None:
    review = json.loads(render_access_review("create", "pods", "ns",
                                             as_service_account=SERVICE_ACCOUNT))
    assert review["kind"] == "SubjectAccessReview"
    assert review["spec"]["user"] == f"system:serviceaccount:ns:{SERVICE_ACCOUNT}"
    # And without a subject it is a *self* review — "can I", not "can they".
    assert json.loads(render_access_review("create", "pods", "ns"))["kind"] == (
        "SelfSubjectAccessReview"
    )


def test_a_subresource_is_its_own_field_not_a_slash_string() -> None:
    """`oc auth can-i` collapses pods/exec onto pods when impersonating and answers yes for a verb
    RBAC denies — measured against OpenShift 4.21. The API object keeps them apart."""
    review = json.loads(render_access_review("create", "pods", "ns", subresource="exec",
                                             as_service_account=SERVICE_ACCOUNT))
    attributes = review["spec"]["resourceAttributes"]
    assert attributes["resource"] == "pods"
    assert attributes["subresource"] == "exec"
    # No subresource must not leave an empty one behind, which some servers treat as a mismatch.
    plain = json.loads(render_access_review("create", "pods", "ns"))
    assert "subresource" not in plain["spec"]["resourceAttributes"]


def test_an_unreachable_review_is_unknown_not_denied() -> None:
    """"Denied" and "we could not find out" call for different messages."""
    with patch("factory.contained.k8s.subprocess.run", side_effect=FileNotFoundError):
        assert k8s.access_review("create", "pods", "ns") is None
    with patch("factory.contained.k8s.subprocess.run", return_value=_completed("true")):
        assert k8s.access_review("create", "pods", "ns") is True
    with patch("factory.contained.k8s.subprocess.run", return_value=_completed("false")):
        assert k8s.access_review("create", "pods", "ns") is False


def test_pods_exec_being_granted_is_itself_a_failure() -> None:
    """The one check that fails when something succeeds."""
    with patch("factory.contained.k8s_setup.access_review", return_value=True):
        check = k8s_setup._no_exec_check("ns")
    assert not check.ok
    assert "recover a shell" in check.detail
    assert check.fix

    with patch("factory.contained.k8s_setup.access_review", return_value=False):
        check = k8s_setup._no_exec_check("ns")
    assert check.ok

    with patch("factory.contained.k8s_setup.access_review", return_value=None):
        check = k8s_setup._no_exec_check("ns")
    assert not check.ok
    assert "could not check" in check.detail


def test_a_secret_with_the_wrong_keys_is_reported_by_key_never_by_value() -> None:
    payload = json.dumps({"SOME_OTHER_KEY": "c2VjcmV0"})
    with patch("factory.contained.k8s_setup._run", return_value=_completed(payload)):
        check = k8s_setup._secret_check("oc", "ns")
    assert not check.ok
    assert "SOME_OTHER_KEY" in check.detail
    assert "c2VjcmV0" not in check.detail
    assert "oc create secret" in (check.fix or "")


def test_a_vertex_secret_is_accepted() -> None:
    payload = json.dumps({k: "x" for k in k8s_setup.VERTEX_KEYS})
    with patch("factory.contained.k8s_setup._run", return_value=_completed(payload)):
        assert k8s_setup._secret_check("oc", "ns").ok


def test_setup_applies_nothing_without_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run") as run:
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=False)
    run.assert_not_called()
    assert code == 1
    captured = capsys.readouterr()
    # The full manifest is still printed — every object, not a summary.
    assert "kind: ServiceAccount" in captured.out
    assert "kind: Role" in captured.out
    assert "kind: PersistentVolumeClaim" in captured.out
    # ...but the outcome is stated *before* it, so 80 lines of YAML cannot bury it.
    assert "Nothing was applied" in captured.err


def test_setup_says_so_when_no_cluster_is_selected(capsys: pytest.CaptureFixture[str]) -> None:
    """"About to apply ... with your own credentials" is untrue when there are none."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("", returncode=1)), \
         patch("factory.contained.k8s_setup.subprocess.run") as run:
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True,
                                   assume_yes=True)
    run.assert_not_called()
    assert code == 1
    assert "No cluster is selected" in capsys.readouterr().err


def test_setup_degrades_to_printing_when_apply_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """It never partially applies and reports success."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run",
               return_value=_completed("", returncode=1)), \
         patch("factory.contained.k8s_setup.verify_k8s",
               return_value=[Check("bundle:role", False, "missing", fix="apply the bundle")]):
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=False,
                                   assume_yes=True)
    assert code == 1
    err = capsys.readouterr().err
    assert "hand the manifest above" in err.lower()


def test_a_sweep_that_matched_nothing_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """`oc delete --ignore-not-found` prints "No resources found" when it matched nothing; echoing
    that verbatim reads as "swept No resources found"."""
    with patch("factory.contained.k8s._run",
               return_value=_completed("No resources found in ns namespace.")):
        k8s.remove_cluster_runtime("rta-test", namespace="ns")
    assert "swept" not in capsys.readouterr().out


def test_a_sweep_that_deleted_something_reports_a_count(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s._run",
               return_value=_completed('pod "a" deleted\npod "b" deleted')):
        k8s.remove_cluster_runtime("rta-test", namespace="ns")
    assert "swept 2 pod(s)" in capsys.readouterr().out
