"""Coverage-completing tests for `factory.contained.k8s`.

The existing suites cover the happy paths and the manifest shapes; this file targets the failure
directions those leave behind — every "the CLI could not be run", "the JSON was junk", "the token
expired", and "the pod never started" branch. Each of these is a place the module deliberately
degrades rather than raising, and a branch that degrades wrongly is exactly the kind of bug that
only shows up on a real cluster having a bad day, so it is worth pinning here.

Nothing in this file may touch a real cluster: everything shells out through `k8s._run` /
`k8s.subprocess.run` / `shutil.which`, and every test patches those. `wait_for_container` polls on
a clock, so its tests patch `time.monotonic` (to drive the deadline instantly) and `time.sleep`
(to a no-op) — the module imports `time` inside the function, so those are patched on the real
`time` module rather than on the k8s namespace.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained import k8s
from factory.contained.runtimes import LifecycleError


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --------------------------------------------------------------------------------------------
# cli_binary — the "neither tool is installed" path
# --------------------------------------------------------------------------------------------


def test_cli_binary_prefers_oc_then_kubectl() -> None:
    with patch("factory.contained.k8s.shutil.which", side_effect=lambda c: c == "oc"):
        assert k8s.cli_binary() == "oc"
    # oc absent, kubectl present — exercises the loop advancing past the first candidate.
    with patch("factory.contained.k8s.shutil.which", side_effect=lambda c: c == "kubectl"):
        assert k8s.cli_binary() == "kubectl"


def test_cli_binary_raises_when_neither_is_on_path() -> None:
    with patch("factory.contained.k8s.shutil.which", return_value=None):
        with pytest.raises(k8s.ClusterError, match="neither"):
            k8s.cli_binary()


# --------------------------------------------------------------------------------------------
# current_namespace
# --------------------------------------------------------------------------------------------


def test_current_namespace_reads_the_context_namespace() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("team-ns\n")):
        assert k8s.current_namespace() == "team-ns"


def test_current_namespace_is_none_when_the_read_fails_or_is_empty() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=None):
        assert k8s.current_namespace() is None
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("   ")):
        assert k8s.current_namespace() is None


# --------------------------------------------------------------------------------------------
# _kubeconfig_json / list_contexts / _first_section / cluster_context
# --------------------------------------------------------------------------------------------


def test_kubeconfig_json_degrades_to_empty_on_a_failed_read() -> None:
    with patch("factory.contained.k8s._run", return_value=None):
        assert k8s._kubeconfig_json(["oc", "config", "view"]) == {}
    with patch("factory.contained.k8s._run", return_value=_completed("{}", 1)):
        assert k8s._kubeconfig_json(["oc", "config", "view"]) == {}


def test_kubeconfig_json_degrades_when_the_top_level_is_not_an_object() -> None:
    with patch("factory.contained.k8s._run", return_value=_completed("[1, 2, 3]")):
        assert k8s._kubeconfig_json(["oc", "config", "view"]) == {}


def test_list_contexts_is_empty_when_no_cli_is_installed() -> None:
    with patch("factory.contained.k8s.cli_binary", side_effect=k8s.ClusterError("no cli")):
        assert k8s.list_contexts() == []


def test_list_contexts_skips_non_dict_entries() -> None:
    payload = json.dumps({
        "contexts": ["junk", {"name": "dev", "context": {"cluster": "c1"}}],
        "clusters": [{"name": "c1", "cluster": {"server": "https://x"}}],
    })
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed(payload)):
        contexts = k8s.list_contexts()
    assert [c.context for c in contexts] == ["dev"]
    assert contexts[0].server == "https://x"


def test_first_section_returns_empty_when_the_nested_value_is_not_a_dict() -> None:
    # entries[0][inner] is present but not a dict — the 303->305 fall-through.
    data = {"contexts": [{"context": "not-a-dict"}]}
    assert k8s._first_section(data, "contexts", "context") == {}
    # And when the list is empty.
    assert k8s._first_section({"contexts": []}, "contexts", "context") == {}


def test_cluster_context_is_empty_when_no_cli_is_installed() -> None:
    with patch("factory.contained.k8s.cli_binary", side_effect=k8s.ClusterError("no cli")):
        assert k8s.cluster_context() == k8s.ClusterContext()


# --------------------------------------------------------------------------------------------
# secret_keys — the failure directions the value-safe reader must not raise on
# --------------------------------------------------------------------------------------------


def test_secret_keys_is_empty_when_no_cli_is_installed() -> None:
    with patch("factory.contained.k8s.cli_binary", side_effect=k8s.ClusterError("no cli")):
        assert k8s.secret_keys(k8s.SECRET_NAME, "ns") == set()


def test_secret_keys_is_empty_when_the_json_is_malformed_but_object_shaped() -> None:
    # Starts with `{` so it passes the shape guard, then fails to parse — the 275-276 branch.
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("{not valid json")):
        assert k8s.secret_keys(k8s.SECRET_NAME, "ns") == set()


# --------------------------------------------------------------------------------------------
# use_context
# --------------------------------------------------------------------------------------------


def test_use_context_reports_when_no_cli_is_installed() -> None:
    with patch("factory.contained.k8s.cli_binary", side_effect=k8s.ClusterError("no cli")):
        ok, detail = k8s.use_context("dev")
    assert ok is False
    assert "no cli" in detail


def test_use_context_reports_when_the_cli_could_not_be_run() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=None):
        ok, detail = k8s.use_context("dev")
    assert ok is False
    assert "config use-context dev" in detail


def test_use_context_succeeds_and_returns_the_output() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed('Switched to "dev".\n')):
        ok, detail = k8s.use_context("dev")
    assert ok is True
    assert detail == 'Switched to "dev".'


def test_use_context_reports_the_failure_detail_or_a_placeholder() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               return_value=_completed("", 1, stderr="no context exists with the name")):
        ok, detail = k8s.use_context("dev")
    assert ok is False
    assert "no context exists" in detail
    # Non-zero but with no stderr at all falls back to the placeholder.
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("", 1)):
        ok, detail = k8s.use_context("dev")
    assert ok is False
    assert detail == "no detail given"


# --------------------------------------------------------------------------------------------
# has_cluster_context
# --------------------------------------------------------------------------------------------


def test_has_cluster_context_reflects_whether_a_context_is_set() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("dev\n")):
        assert k8s.has_cluster_context() is True
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=None):
        assert k8s.has_cluster_context() is False


# --------------------------------------------------------------------------------------------
# resolve_namespace — the two distinct "no usable namespace" messages
# --------------------------------------------------------------------------------------------


def test_resolve_namespace_blames_the_flag_only_when_a_flag_was_given() -> None:
    with patch("factory.contained.k8s.current_namespace", return_value=None):
        # An explicit-but-empty value points the finger at the flag, not the user.
        with pytest.raises(k8s.ClusterError, match="--namespace was given"):
            k8s.resolve_namespace("")
        # No flag at all gets the "pass --namespace" guidance instead.
        with pytest.raises(k8s.ClusterError, match="no namespace given"):
            k8s.resolve_namespace(None)


def test_resolve_namespace_prefers_the_explicit_value() -> None:
    assert k8s.resolve_namespace("mine") == "mine"


# --------------------------------------------------------------------------------------------
# _run — the subprocess-failed swallow
# --------------------------------------------------------------------------------------------


def test_run_returns_none_when_the_subprocess_cannot_be_launched() -> None:
    with patch("factory.contained.k8s.subprocess.run", side_effect=FileNotFoundError):
        assert k8s._run(["oc", "get", "pods"]) is None
    with patch("factory.contained.k8s.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="oc", timeout=1)):
        assert k8s._run(["oc", "get", "pods"]) is None


def test_run_returns_the_completed_process_on_success() -> None:
    with patch("factory.contained.k8s.subprocess.run", return_value=_completed("ok")):
        result = k8s._run(["oc", "version"])
    assert result is not None and result.stdout == "ok"


# --------------------------------------------------------------------------------------------
# Command composition — the pure argv builders
# --------------------------------------------------------------------------------------------


def test_build_apply_argv_targets_stdin_in_the_namespace() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"):
        assert k8s.build_apply_argv("ns") == ["oc", "apply", "-n", "ns", "-f", "-"]


def test_build_pod_exec_argv_uses_a_bare_i_without_a_tty() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"):
        argv = k8s.build_pod_exec_argv("pod", "ns", ["ls"], tty=False)
    assert "-i" in argv
    assert "-t" not in argv
    assert argv[-2:] == ["--", "ls"]


def test_render_access_review_carries_an_explicit_api_group() -> None:
    review = json.loads(
        k8s.render_access_review("create", "builds", "ns", group="build.openshift.io")
    )
    assert review["spec"]["resourceAttributes"]["group"] == "build.openshift.io"


# --------------------------------------------------------------------------------------------
# access_review — the "denied vs could-not-find-out" split
# --------------------------------------------------------------------------------------------


def test_access_review_is_none_when_the_review_command_fails() -> None:
    with patch("factory.contained.k8s.subprocess.run",
               return_value=_completed("", 1, stderr="boom")):
        assert k8s.access_review("create", "pods", "ns") is None


# --------------------------------------------------------------------------------------------
# namespace_fs_group — reading the OpenShift supplemental-groups range
# --------------------------------------------------------------------------------------------


def test_namespace_fs_group_parses_the_range_start() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("1000700000/10000\n")):
        assert k8s.namespace_fs_group("ns") == 1000700000


def test_namespace_fs_group_is_none_when_the_annotation_is_absent_or_junk() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=None):
        assert k8s.namespace_fs_group("ns") is None
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("", 1)):
        assert k8s.namespace_fs_group("ns") is None
    # Present but not an integer — plain Kubernetes has no such annotation.
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("not-a-number\n")):
        assert k8s.namespace_fs_group("ns") is None


# --------------------------------------------------------------------------------------------
# apply_manifest
# --------------------------------------------------------------------------------------------


def test_apply_manifest_succeeds_quietly() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run", return_value=_completed("configured")):
        k8s.apply_manifest("kind: Pod\n", "ns")  # no raise


def test_apply_manifest_raises_when_the_cli_cannot_be_run() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run", side_effect=FileNotFoundError("oc")):
        with pytest.raises(k8s.ClusterError, match="applying the manifest failed"):
            k8s.apply_manifest("kind: Pod\n", "ns")


def test_apply_manifest_raises_on_a_nonzero_exit() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run",
               return_value=_completed("", 1, stderr="forbidden")):
        with pytest.raises(k8s.ClusterError, match="forbidden"):
            k8s.apply_manifest("kind: Pod\n", "ns")


# --------------------------------------------------------------------------------------------
# wait_for_container — the polled loop, driven instantly by a patched clock
# --------------------------------------------------------------------------------------------


def _pod_json(*, name: str = "factory", state: dict | None = None, phase: str = "Pending") -> str:
    status: dict = {"phase": phase}
    if state is not None:
        status["containerStatuses"] = [{"name": name, "state": state}]
    return json.dumps({"status": status})


def test_wait_for_container_returns_running_when_the_container_is_up() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               return_value=_completed(_pod_json(state={"running": {}}))), \
         patch("time.sleep"), patch("time.monotonic", side_effect=[0, 1]):
        assert k8s.wait_for_container("p", "ns", "factory") == "running"


def test_wait_for_container_returns_terminated_on_a_clean_exit() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               return_value=_completed(_pod_json(state={"terminated": {"exitCode": 0}}))), \
         patch("time.sleep"), patch("time.monotonic", side_effect=[0, 1]):
        assert k8s.wait_for_container("p", "ns", "factory") == "terminated"


def test_wait_for_container_tolerates_a_missing_read_and_junk_json_then_succeeds() -> None:
    """A dropped read and a half-written document are both "keep polling", not failures."""
    running = _completed(_pod_json(state={"running": {}}))
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               side_effect=[None, _completed("not json"), running]), \
         patch("time.sleep") as slept, \
         patch("time.monotonic", side_effect=[0, 1, 2, 3]):
        assert k8s.wait_for_container("p", "ns", "factory", timeout=100) == "running"
    assert slept.call_count == 2          # once for the dropped read, once for the junk


def test_wait_for_container_times_out_without_a_last_state() -> None:
    """When the deadline is already past, there is no last state to report — the 886 falsy branch."""
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("time.sleep"), patch("time.monotonic", side_effect=[0, 0]):
        with pytest.raises(k8s.ClusterError) as raised:
            k8s.wait_for_container("p", "ns", "factory", timeout=0)
    assert "last state" not in str(raised.value)


# --------------------------------------------------------------------------------------------
# stream_workspace / fetch_workspace — the tarball transport
# --------------------------------------------------------------------------------------------


def test_stream_workspace_succeeds(tmp_path: Path) -> None:
    tarball = tmp_path / "ws.tar.gz"
    tarball.write_bytes(b"payload")
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run", return_value=_completed("done")):
        k8s.stream_workspace(tarball, "pod", "ns")  # no raise


def test_stream_workspace_raises_with_a_retry_hint_on_failure(tmp_path: Path) -> None:
    tarball = tmp_path / "ws.tar.gz"
    tarball.write_bytes(b"payload")
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run",
               return_value=_completed("", 1, stderr="broken pipe")):
        with pytest.raises(k8s.ClusterError, match="retrying is safe"):
            k8s.stream_workspace(tarball, "pod", "ns")


def test_fetch_workspace_succeeds(tmp_path: Path) -> None:
    destination = tmp_path / "out.tar.gz"
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, b"", b"")):
        k8s.fetch_workspace("pod", "ns", destination)  # no raise
    assert destination.exists()


def test_fetch_workspace_raises_on_failure(tmp_path: Path) -> None:
    destination = tmp_path / "out.tar.gz"
    # stderr is bytes here — the call runs without text=True — so the error path must .decode() it.
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s.subprocess.run",
               return_value=subprocess.CompletedProcess([], 1, b"", b"no such pod")):
        with pytest.raises(k8s.ClusterError, match="no such pod"):
            k8s.fetch_workspace("pod", "ns", destination)


# --------------------------------------------------------------------------------------------
# _summarize
# --------------------------------------------------------------------------------------------


def test_summarize_returns_the_last_meaningful_line() -> None:
    stderr = "E0812 noise line\nUnhandled Error in something\nerror: the real problem\n"
    assert k8s._summarize(stderr) == "the real problem"


def test_summarize_reports_a_placeholder_when_there_is_nothing_useful() -> None:
    assert k8s._summarize("E0812 only noise\n\n") == "no details given"


# --------------------------------------------------------------------------------------------
# cluster_runtimes — the error surface
# --------------------------------------------------------------------------------------------


def test_cluster_runtimes_raises_lifecycle_error_when_no_namespace_resolves() -> None:
    with patch("factory.contained.k8s.current_namespace", return_value=None):
        with pytest.raises(LifecycleError):
            k8s.cluster_runtimes(None)


def test_cluster_runtimes_raises_when_the_cluster_does_not_answer() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=None):
        with pytest.raises(LifecycleError, match="did not answer"):
            k8s.cluster_runtimes("ns")


def test_cluster_runtimes_summarizes_an_unreachable_cluster() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               return_value=_completed("", 1, stderr="error: Unauthorized")):
        with pytest.raises(LifecycleError, match="cannot reach the cluster"):
            k8s.cluster_runtimes("ns")


def test_cluster_runtimes_raises_on_non_json_output() -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed("not json")):
        with pytest.raises(LifecycleError, match="isn't JSON"):
            k8s.cluster_runtimes("ns")


def test_cluster_runtimes_tolerates_missing_and_malformed_timestamps() -> None:
    payload = json.dumps({"items": [
        {"metadata": {"name": "a", "labels": {}}, "status": {"phase": "Running"}},
        {"metadata": {"name": "b", "creationTimestamp": "not-a-date"},
         "status": {"phase": "Pending"}},
    ]})
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", return_value=_completed(payload)):
        runtimes = k8s.cluster_runtimes("ns")
    assert [r.name for r in runtimes] == ["a", "b"]
    assert all(r.created is None for r in runtimes)


# --------------------------------------------------------------------------------------------
# remove_cluster_runtime — the failure paths
# --------------------------------------------------------------------------------------------


def test_remove_cluster_runtime_reports_a_failed_delete(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Sweep could not be run (None), so the sweep-report block is skipped; the delete then fails.
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run",
               side_effect=[None, _completed("", 1, stderr="forbidden")]):
        code = k8s.remove_cluster_runtime("rta-test", namespace="ns")
    assert code == 1
    assert "deleting pod rta-test failed" in capsys.readouterr().err


def test_remove_cluster_runtime_reports_when_the_delete_cli_could_not_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.k8s.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s._run", side_effect=[None, None]):
        code = k8s.remove_cluster_runtime("rta-test", namespace="ns")
    assert code == 1
    assert "the CLI could not be run" in capsys.readouterr().err


# --------------------------------------------------------------------------------------------
# sync_cluster_runtime
# --------------------------------------------------------------------------------------------


def test_sync_cluster_runtime_fetches_and_reports_where_it_landed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.workspace.contained_home", return_value=tmp_path), \
         patch("factory.contained.k8s.fetch_workspace") as fetch:
        code = k8s.sync_cluster_runtime("rta-test", namespace="ns")
    assert code == 0
    fetch.assert_called_once()
    out = capsys.readouterr().out
    assert "workspace fetched to" in out
    assert "Nothing is merged automatically." in out


def test_sync_cluster_runtime_reports_a_fetch_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("factory.contained.workspace.contained_home", return_value=tmp_path), \
         patch("factory.contained.k8s.fetch_workspace",
               side_effect=k8s.ClusterError("no such pod")):
        code = k8s.sync_cluster_runtime("rta-test", namespace="ns")
    assert code == 1
    assert "no such pod" in capsys.readouterr().err
