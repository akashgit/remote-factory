"""The cluster runtime and the cluster division: manifests, transport, RBAC, and the boundary."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from factory.cli import contained as cli
from factory.cli.contained_k8s import PACK_EXCLUDES, _build_pod_plan, _pack
from factory.contained import k8s, k8s_credentials, k8s_setup, secrets
from factory.contained.bundle import SCC_ROLEBINDING, render_bundle
from factory.contained.k8s import (
    FACTORY_CONTAINER,
    LABEL_CONTAINED,
    LOADER_CONTAINER,
    PVC_NAME,
    SECRET_NAME,
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


# Bound at import, before the autouse fixture below replaces the module attribute: the one test
# that exercises the real lookup has to be able to reach past its own stub.
_REAL_NAMESPACE_STATUS = k8s_setup._namespace_status


@pytest.fixture(autouse=True)
def _no_cluster_round_trip():
    """Building a pod plan must not phone a cluster.

    `_build_pod_plan` reads the namespace's allocated `fsGroup` range, which is a live `oc get
    namespace`. On a machine logged in to a slow or unreachable cluster that is a 30-second timeout
    per test — the difference between this file taking one second and taking two minutes.
    """
    with patch("factory.cli.contained_k8s.namespace_fs_group", return_value=None):
        yield



@pytest.fixture(autouse=True)
def _no_real_kubeconfig():
    """Keep these tests off the developer's actual kubeconfig.

    Two reasons, both found by running it. `_choose_context` reads the real kubeconfig, so on a
    machine with several clusters an interactive `setup_k8s` test stops at a prompt. And every one
    of these helpers shells out to `oc`, which costs seconds per call on macOS — enough to take
    this file from five seconds to seven minutes. Tests that mean to exercise a chooser or assert
    on a server patch these themselves; an inner `patch` wins over the fixture.
    """
    with patch("factory.contained.k8s_setup.list_contexts", return_value=[]), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=k8s.ClusterContext()), \
         patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.PRESENT), \
         patch("factory.contained.k8s._run", return_value=_completed("true")), \
         patch("factory.contained.k8s_credentials._run", return_value=_completed("{}")), \
         patch("factory.contained.k8s_setup.run_credentials_step", return_value=False), \
         patch("factory.contained.k8s_setup.access_review", return_value=True):
        # The credentials step is stubbed for the same two reasons as the rest: it reads the
        # cluster, and it is a conversation. A `setup_k8s` test that let it run would consume the
        # mocked `input()` the object walk is asserting on, and would block on a prompt that never
        # receives a valid key. `tests/test_contained_k8s_credentials.py` exercises it directly.
        # `access_review` is stubbed under the name *k8s_setup* imported, not on `k8s` itself: it
        # shells out with `subprocess.run` directly, so nothing else here catches it, and the test
        # that exercises the real function reaches it through `k8s.access_review`, untouched.
        yield


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
            # `NotFound` specifically: any non-zero used to count as missing, which turned an
            # expired login into a namespace that appeared to hold nothing.
            missing = _completed(returncode=1)
            missing.stderr = f'Error from server (NotFound): rolebindings "{SCC_ROLEBINDING}" not found'
            return missing
        return _completed("ok")

    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", side_effect=fake_run):
        # `probe_inference=False`: the probe launches a real pod and waits on it, which is a
        # three-minute round trip and nothing to do with what this test asserts.
        checks = k8s_setup.verify_k8s(namespace="ns", probe_inference=False)
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
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(payload)):
        check = k8s_credentials.secret_check("oc", "ns")
    assert not check.ok
    assert "SOME_OTHER_KEY" in check.detail
    assert "c2VjcmV0" not in check.detail
    assert "oc create secret" in (check.fix or "")


def test_a_vertex_secret_is_accepted() -> None:
    payload = json.dumps({k: "x" for k in k8s_setup.VERTEX_KEYS})
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(payload)):
        assert k8s_credentials.secret_check("oc", "ns").ok


def test_setup_reports_the_current_state_before_asking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary covers every object, so "4 of 5 are already there" is visible up front."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run"), \
         patch("builtins.input", return_value="q"):
        k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    printed = capsys.readouterr().out
    for ref in ("serviceaccount/factory", "role/factory-runtime", "rolebinding/factory-scc",
                "pvc/factory-workspace"):
        assert ref in printed
    # The state is established before the first item is walked, not after it. Asserted on the item
    # header rather than the prompt: the prompt is written by `input()`, which the mock swallows.
    assert printed.index("Comparing 5 object(s)") < printed.index("1 of 5")


def test_setup_asks_once_per_object_that_needs_a_decision(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run"), \
         patch("factory.contained.k8s_setup.verify_k8s", return_value=[]), \
         patch("builtins.input", return_value="n") as ask:
        k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    assert ask.call_count == 5          # one per object, not one for the whole wall of YAML


def test_setup_explains_each_object_before_asking_about_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The YAML says a Role has these verbs; the purpose says why a run needs them."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run"), \
         patch("builtins.input", return_value="q"):
        k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    printed = capsys.readouterr().out
    assert "The identity the factory's pod runs as" in printed


def test_setup_asks_which_namespace_when_none_was_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Landing silently on whatever `oc project` is set to is how `default` acquires a PVC."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run"), \
         patch("builtins.input", side_effect=["factory-contained", "q"]):
        k8s_setup.setup_k8s(namespace=None, division=False, interactive=True)
    printed = capsys.readouterr().out
    assert "namespace 'factory-contained'" in printed
    assert "namespace 'default'" not in printed


def test_an_empty_answer_takes_the_current_context() -> None:
    with patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("builtins.input", return_value=""):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") == "default"


def test_an_explicit_namespace_is_used_without_being_asked_about() -> None:
    """`--namespace` settles *which* namespace; it is never re-litigated by a prompt."""
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.PRESENT), \
         patch("builtins.input", side_effect=AssertionError("must not ask")):
        assert k8s_setup._choose_namespace("mine", interactive=True, binary="oc") == "mine"


def test_an_explicit_namespace_is_still_checked_for_existence() -> None:
    """A typo would otherwise surface as five separate NotFound errors from the apply."""
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace",
               return_value=(True, "created")) as create, \
         patch("factory.contained.style.confirm", return_value=True):
        assert k8s_setup._choose_namespace("mine", interactive=True, binary="oc") == "mine"
    create.assert_called_once()


def test_declining_to_create_a_missing_namespace_stops_rather_than_proceeding() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace") as create, \
         patch("factory.contained.style.confirm", return_value=False):
        assert k8s_setup._choose_namespace("mine", interactive=True, binary="oc") is None
    create.assert_not_called()


def test_a_missing_namespace_is_offered_for_creation_then_reused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace", return_value=(True, "")), \
         patch("factory.contained.style.confirm", return_value=True), \
         patch("builtins.input", return_value="factory-yi"):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") == "factory-yi"
    assert "does not exist on this cluster" in capsys.readouterr().out


def test_refusing_creation_at_the_prompt_asks_for_another_namespace() -> None:
    """Declining is not aborting: the obvious next move is to name a different one."""
    with patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("factory.contained.k8s_setup._namespace_status",
               side_effect=[k8s_setup.ABSENT, k8s_setup.PRESENT]), \
         patch("factory.contained.style.confirm", return_value=False), \
         patch("builtins.input", side_effect=["typo", "real-one"]):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") == "real-one"


def test_a_namespace_we_may_not_read_is_not_treated_as_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On OpenShift a regular user is routinely denied `get namespaces` for a project they own."""
    with patch("factory.contained.k8s_setup._namespace_status",
               return_value=k8s_setup.UNREADABLE), \
         patch("factory.contained.k8s_setup._create_namespace") as create:
        assert k8s_setup._choose_namespace("mine", interactive=True, binary="oc") == "mine"
    create.assert_not_called()
    assert "Could not confirm" in capsys.readouterr().out


def test_namespace_status_falls_back_to_project_when_namespaces_are_forbidden() -> None:
    forbidden = _completed("", 1)
    forbidden = subprocess.CompletedProcess([], 1, "", 'namespaces "x" is forbidden')
    found = _completed("project.project.openshift.io/x")
    with patch("factory.contained.k8s_setup._run", side_effect=[forbidden, found]) as run:
        assert _REAL_NAMESPACE_STATUS("x", "oc") == k8s_setup.PRESENT
    assert run.call_args_list[1][0][0][:3] == ["oc", "get", "project"]


def test_namespace_creation_uses_new_project_on_openshift() -> None:
    """A regular user is usually denied a bare Namespace but permitted to request a Project."""
    with patch("factory.contained.k8s_setup._run", return_value=_completed("ok")) as run:
        assert k8s_setup._create_namespace("mine", "oc")[0] is True
    assert run.call_args[0][0] == ["oc", "new-project", "mine"]
    with patch("factory.contained.k8s_setup._run", return_value=_completed("ok")) as run:
        k8s_setup._create_namespace("mine", "kubectl")
    assert run.call_args[0][0] == ["kubectl", "create", "namespace", "mine"]


def test_setup_names_the_cluster_not_only_the_namespace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`default` exists on every cluster anyone has logged into; the server is what identifies one."""
    context = k8s.ClusterContext(
        context="dev", server="https://api.example.com:6443", user="you@example.com",
        namespace="default",
    )
    with patch("factory.contained.k8s_setup.cluster_context", return_value=context), \
         patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("builtins.input", return_value=""):
        k8s_setup._choose_namespace(None, interactive=True, binary="oc")
    printed = capsys.readouterr().out
    assert "https://api.example.com:6443" in printed
    assert "you@example.com" in printed
    assert "dev" in printed


def test_the_review_summary_names_the_cluster(capsys: pytest.CaptureFixture[str]) -> None:
    """With a per-object walk there is no single irreversible moment left to attach it to — the
    first `y` is already one — so the destination is stated before the walk begins."""
    context = k8s.ClusterContext(server="https://api.example.com:6443")
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=context), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run"), \
         patch("builtins.input", return_value="q"):
        k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    assert "https://api.example.com:6443" in capsys.readouterr().out


def test_a_walked_run_is_not_asked_to_confirm_a_second_time() -> None:
    """Every accepted object was confirmed a moment ago; a blanket prompt on top teaches `y`."""
    def absent(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        # Nothing is in the namespace, so all five objects need a decision.
        return _completed("", 1) if argv[1] == "get" else _completed("applied")

    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_review._run", side_effect=absent), \
         patch("factory.contained.k8s_setup.subprocess.run", return_value=_completed("applied")), \
         patch("factory.contained.k8s_setup.verify_k8s",
               return_value=[Check("namespace", True, "ok")]), \
         patch("builtins.input", side_effect=["a"]) as ask:
        k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    assert ask.call_count == 1          # the single `a`, and nothing after it


def test_an_unreadable_kubeconfig_still_reports_what_it_knows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Degrades one field at a time rather than printing nothing at all."""
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=k8s.ClusterContext()), \
         patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("builtins.input", return_value=""):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") == "default"
    assert "'default'" in capsys.readouterr().out


def test_cluster_context_reads_names_never_credential_material() -> None:
    payload = json.dumps({
        "current-context": "dev",
        "contexts": [{"context": {"user": "you", "namespace": "ns"}}],
        "clusters": [{"cluster": {"server": "https://api.example.com:6443"}}],
        "users": [{"user": {"token": "sk-secret-token"}}],
    })
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed(payload)):
        context = k8s.cluster_context()
    assert context.server == "https://api.example.com:6443"
    assert (context.context, context.user, context.namespace) == ("dev", "you", "ns")
    # Nothing from the `users` section reaches the dataclass at all.
    assert "sk-secret-token" not in repr(context)


def test_cluster_context_degrades_to_empty_on_junk() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("not json")):
        assert k8s.cluster_context() == k8s.ClusterContext()


def test_a_google_credential_is_mounted_as_a_file_not_an_env_var(tmp_path: Path) -> None:
    """`GOOGLE_APPLICATION_CREDENTIALS` is a *path*. Passing the JSON as its value cannot work.

    Verified against a live cluster: without this the pod got the variable set to the credential's
    text, and the auth library tried to open a file named `{"type": "authorized_user"…}`.
    """
    plan = _plan(tmp_path)
    plan = replace(plan, adc=True, env={**plan.env, "GOOGLE_APPLICATION_CREDENTIALS": k8s.ADC_PATH})
    doc = yaml.safe_load(render_pod(plan))

    volume = next(v for v in doc["spec"]["volumes"] if v["name"] == "credentials")
    assert volume["secret"]["secretName"] == SECRET_NAME
    assert volume["secret"]["defaultMode"] == 0o400
    # No `items:` — a volume naming a key the Secret lacks leaves the pod Pending on "couldn't
    # find key", and `optional` covers a missing Secret, not a missing key.
    assert "items" not in volume["secret"]

    factory = next(c for c in doc["spec"]["containers"] if c["name"] == FACTORY_CONTAINER)
    mount = next(m for m in factory["volumeMounts"] if m["name"] == "credentials")
    assert mount["mountPath"] == k8s.CREDENTIALS_MOUNT
    assert mount["readOnly"] is True
    env = {e["name"]: e["value"] for e in factory["env"]}
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == f"{k8s.CREDENTIALS_MOUNT}/{k8s.ADC_SECRET_KEY}"


def test_no_credential_volume_when_the_secret_carries_no_file(tmp_path: Path) -> None:
    """An API-key run must not grow a mount it has no use for."""
    doc = yaml.safe_load(render_pod(_plan(tmp_path)))
    assert [v["name"] for v in doc["spec"]["volumes"]] == ["workspace"]
    factory = next(c for c in doc["spec"]["containers"] if c["name"] == FACTORY_CONTAINER)
    assert [m["name"] for m in factory["volumeMounts"]] == ["workspace"]


def test_the_adc_key_is_a_legal_environment_variable_name() -> None:
    """`envFrom` maps every key to a variable and skips illegal names.

    A key called `application_default_credentials.json` would attach an
    `InvalidEnvironmentVariableNames` event to a pod that is in fact fine.
    """
    assert k8s.ADC_SECRET_KEY.replace("_", "a").isalnum()
    assert not k8s.ADC_SECRET_KEY[0].isdigit()


def test_vertex_configuration_without_a_credential_is_not_enough() -> None:
    """The three config variables only say which endpoint to talk to; none authenticates."""
    config_only = json.dumps({
        k: "x" for k in
        ("CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID")
    })
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(config_only)):
        assert not k8s_credentials.secret_check("oc", "ns").ok
    # With the credential file, it passes.
    complete = json.dumps({k: "x" for k in k8s_setup.VERTEX_KEYS})
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(complete)):
        assert k8s_credentials.secret_check("oc", "ns").ok


def test_secret_keys_reads_names_and_never_values() -> None:
    payload = json.dumps({"ANTHROPIC_API_KEY": "c2stYW50LXNlY3JldA==",
                          k8s.ADC_SECRET_KEY: "eyJ0eXBlIjogImF1dGhvcml6ZWRfdXNlciJ9"})
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed(payload)):
        keys = k8s.secret_keys(SECRET_NAME, "ns")
    assert keys == {"ANTHROPIC_API_KEY", k8s.ADC_SECRET_KEY}


def test_secret_keys_degrades_to_empty_rather_than_raising() -> None:
    for outcome in (None, _completed("", 1), _completed("not json")):
        with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
             patch("factory.contained.k8s._run", return_value=outcome):
            assert k8s.secret_keys(SECRET_NAME, "ns") == set()


def test_verify_reports_each_check_as_it_lands() -> None:
    """A step that prints nothing for three minutes is read as a hang. It was."""
    seen: list[str] = []
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("", 1)):
        checks = k8s_setup.verify_k8s(namespace="ns", probe_inference=False,
                                      on_check=lambda c: seen.append(c.name))
    # Every result reached the callback, in order, and none was reported only at the end.
    assert seen == [c.name for c in checks]
    assert seen


def test_a_check_that_short_circuits_still_reaches_the_callback() -> None:
    """A streaming caller prints only the summary at the end; a skipped callback is a lost check."""
    seen: list[str] = []
    with patch("factory.contained.k8s_setup.cli_binary",
               side_effect=k8s.ClusterError("neither oc nor kubectl")):
        checks = k8s_setup.verify_k8s(namespace="ns", on_check=lambda c: seen.append(c.name))
    assert seen == ["cluster_cli"] == [c.name for c in checks]


def test_the_inference_probe_is_skipped_when_the_secret_is_missing() -> None:
    """The probe pod mounts that Secret; without it the wait is 180s to learn what we know."""
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("ctx")), \
         patch("factory.contained.k8s_setup.secret_check",
               return_value=Check("credentials_secret", False, "missing", fix="oc create secret")), \
         patch("factory.contained.k8s_setup._inference_check") as probe:
        checks = k8s_setup.verify_k8s(namespace="ns")
    probe.assert_not_called()
    inference = next(c for c in checks if c.name == "inference_from_cluster")
    assert not inference.ok
    assert "not attempted" in inference.detail
    assert inference.fix == "oc create secret"     # the fix is the Secret's, not a generic one


def test_the_inference_probe_still_runs_when_the_secret_is_there() -> None:
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("ctx")), \
         patch("factory.contained.k8s_setup.secret_check",
               return_value=Check("credentials_secret", True, "present")), \
         patch("factory.contained.k8s_setup._inference_check",
               return_value=Check("inference_from_cluster", True, "reached")) as probe:
        k8s_setup.verify_k8s(namespace="ns")
    probe.assert_called_once()


def test_every_cluster_command_carries_the_chosen_context() -> None:
    """Choosing a cluster is worthless if the apply still goes to the current one."""
    try:
        k8s.set_active_context("other-cluster")
        assert k8s.cli("oc", "apply", "-f", "-") == [
            "oc", "--context", "other-cluster", "apply", "-f", "-"
        ]
    finally:
        k8s.set_active_context(None)
    assert k8s.cli("oc", "apply", "-f", "-") == ["oc", "apply", "-f", "-"]


def test_list_contexts_pairs_each_context_with_its_server() -> None:
    payload = json.dumps({
        "contexts": [
            {"name": "dev", "context": {"cluster": "c1", "user": "u1", "namespace": "ns1"}},
            {"name": "prod", "context": {"cluster": "c2", "user": "u2"}},
        ],
        "clusters": [
            {"name": "c1", "cluster": {"server": "https://dev.example.com"}},
            {"name": "c2", "cluster": {"server": "https://prod.example.com"}},
        ],
    })
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed(payload)):
        contexts = k8s.list_contexts()
    assert [c.context for c in contexts] == ["dev", "prod"]
    assert [c.server for c in contexts] == ["https://dev.example.com", "https://prod.example.com"]
    assert contexts[1].namespace is None          # a context need not pin a namespace


def test_a_single_context_is_not_worth_a_question() -> None:
    one = [k8s.ClusterContext(context="only", server="https://x")]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=one), \
         patch("builtins.input", side_effect=AssertionError("must not ask")):
        assert k8s_setup._choose_context(interactive=True) is None


def test_a_cluster_can_be_chosen_by_number_or_by_name() -> None:
    contexts = [
        k8s.ClusterContext(context="dev", server="https://dev"),
        k8s.ClusterContext(context="prod", server="https://prod"),
    ]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=contexts), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=contexts[0]):
        with patch("factory.contained.style.read_line", return_value="2"):
            assert k8s_setup._choose_context(interactive=True) == "prod"
        # People paste context names as often as they count list positions.
        with patch("factory.contained.style.read_line", return_value="prod"):
            assert k8s_setup._choose_context(interactive=True) == "prod"


def test_escape_at_the_cluster_chooser_stops_setup() -> None:
    contexts = [k8s.ClusterContext(context="dev"), k8s.ClusterContext(context="prod")]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=contexts), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=contexts[0]), \
         patch("factory.contained.style.read_line", return_value=None):
        assert k8s_setup._choose_context(interactive=True) is k8s_setup._ABORT


def test_choosing_a_cluster_never_rewrites_the_kubeconfig() -> None:
    """Where *this* run goes must not change where the user's next unrelated `oc` goes."""
    contexts = [k8s.ClusterContext(context="dev"), k8s.ClusterContext(context="prod")]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=contexts), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=contexts[0]), \
         patch("factory.contained.k8s_setup.use_context") as switch, \
         patch("factory.contained.style.read_line", return_value="2"):
        k8s_setup._choose_context(interactive=True)
    switch.assert_not_called()


def test_declining_the_default_switch_prints_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=k8s.ClusterContext(context="dev")), \
         patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.use_context") as switch, \
         patch("factory.contained.style.confirm", return_value=False):
        k8s_setup._offer_default_switch("prod", interactive=True)
    switch.assert_not_called()
    assert "oc config use-context prod" in capsys.readouterr().out


def test_no_switch_is_offered_when_the_chosen_context_is_already_current(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=k8s.ClusterContext(context="prod")), \
         patch("builtins.input", side_effect=AssertionError("must not ask")):
        k8s_setup._offer_default_switch("prod", interactive=True)
    assert capsys.readouterr().out == ""


def test_ctrl_c_exits_cleanly_rather_than_unwinding(capsys: pytest.CaptureFixture[str]) -> None:
    """Backing out of a wizard partway is ordinary; a stack trace reads as a crash."""
    args = _args(["--target", "k8s", "setup"])
    with patch("factory.cli.contained.run_setup", side_effect=KeyboardInterrupt):
        assert cli.cmd_contained(args) == 130
    assert "Stopped." in capsys.readouterr().err


def test_a_closed_stdin_stops_rather_than_re_asking_forever() -> None:
    """Re-prompting a stream that can never answer is a hang, not a retry."""
    with patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("builtins.input", side_effect=EOFError):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") is None


def test_escape_at_the_namespace_prompt_stops_setup() -> None:
    """Escape has to work at every prompt, not only at the per-object ones."""
    with patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("factory.contained.style.read_line", return_value=None):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") is None


def test_the_namespace_is_marked_as_a_value_not_prose(capsys: pytest.CaptureFixture[str]) -> None:
    """"in namespace default" cannot be read; the quotes are what make `default` a name."""
    with patch("factory.contained.k8s_setup.current_namespace", return_value="default"), \
         patch("builtins.input", return_value=""):
        k8s_setup._choose_namespace(None, interactive=True, binary="oc")
    assert "'default'" in capsys.readouterr().out


def test_setup_applies_nothing_without_confirmation(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("some-context")), \
         patch("factory.contained.k8s_setup.subprocess.run") as run:
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=False)
    # Not `assert_not_called`: establishing the current state legitimately runs `get` and `diff`.
    # What must not have happened is the mutation.
    assert not any("apply" in call.args[0] for call in run.call_args_list if call.args)
    assert code == 1
    captured = capsys.readouterr()
    # Every object is still accounted for — as state, not as a wall of YAML.
    for ref in ("serviceaccount/factory", "role/factory-runtime", "pvc/factory-workspace"):
        assert ref in captured.out
    assert "nothing was applied" in captured.err.lower()


def test_setup_says_so_when_no_cluster_is_selected(capsys: pytest.CaptureFixture[str]) -> None:
    """"About to apply ... with your own credentials" is untrue when there are none.

    The gate asks the *cluster*, not the kubeconfig: `config current-context` reads a local file
    and passes happily for a context whose token expired hours ago, which is precisely the state
    where every apply below it would fail.
    """
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._run", return_value=_completed("", returncode=1)), \
         patch("factory.contained.k8s_setup.login_status",
               return_value=(False, "You must be logged in to the server (Unauthorized)")), \
         patch("factory.contained.k8s_setup.subprocess.run") as run:
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True,
                                   assume_yes=True)
    run.assert_not_called()
    assert code == 1
    assert "no working credential" in capsys.readouterr().err


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
    # "the manifest above" no longer exists — the wall of YAML is gone, so the hand-off names the
    # `bundle` command that reproduces it instead.
    assert "hand the bundle to whoever owns" in err.lower()


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
