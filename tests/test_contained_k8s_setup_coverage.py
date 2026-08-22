"""Coverage for the failure, degradation and interactive branches of `k8s_setup`.

Written against the real branch source (the 4-step wizard with the login gate, the `poll_pod`-based
inference probe, and the credentials step). Everything that would reach a cluster or a prompt is
mocked at the module boundary — a test that reached either would hang, since `conftest` forces the
raw-terminal path off and there is no cluster to answer.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from factory.contained import k8s_setup
from factory.contained.k8s import (
    DOOMED,
    SUCCEEDED,
    WAITING,
    ClusterContext,
    ClusterError,
    PodProgress,
)
from factory.contained.prereq import Check


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture(autouse=True)
def _off_cluster():
    """Safe defaults so nothing reaches a cluster or a prompt; individual tests override as needed.

    Mirrors the intent of `test_contained_k8s.py`'s autouse fixtures, which this file does not
    inherit. Also clears the process-global pinned context that `setup_k8s` sets, so one test's
    choice does not leak into the next.
    """
    k8s_setup.set_active_context(None)
    with patch("factory.contained.k8s_setup.cli_binary", return_value="oc"), \
         patch("factory.contained.k8s_setup.list_contexts", return_value=[]), \
         patch("factory.contained.k8s_setup.cluster_context", return_value=ClusterContext()), \
         patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("factory.contained.k8s_setup.access_review", return_value=True), \
         patch("factory.contained.k8s_setup.gitleaks_available", return_value=True), \
         patch("factory.contained.k8s_setup.resolve_image", return_value="img:latest"):
        yield
    k8s_setup.set_active_context(None)


# ---------------------------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------------------------


def test_run_swallows_a_launch_failure() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=OSError("boom")):
        assert k8s_setup._run(["oc", "version"]) is None


# ---------------------------------------------------------------------------------------------
# verify_k8s — the early returns and the division branch
# ---------------------------------------------------------------------------------------------


def test_verify_stops_when_no_cli_is_installed() -> None:
    with patch("factory.contained.k8s_setup.cli_binary",
               side_effect=ClusterError("neither oc nor kubectl")):
        checks = k8s_setup.verify_k8s(namespace="ns")
    assert [c.name for c in checks] == ["cluster_cli"]
    assert not checks[0].ok


def test_verify_stops_when_the_login_has_expired() -> None:
    """A bad credential must halt before the object checks turn Unauthorized into "missing"."""
    with patch("factory.contained.k8s_setup._run", return_value=_completed("ctx")), \
         patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="c", server="https://s")), \
         patch("factory.contained.k8s_setup.login_status",
               return_value=(False, "Unauthorized")):
        checks = k8s_setup.verify_k8s(namespace="ns")
    assert [c.name for c in checks] == ["cluster_cli", "cluster_login"]
    assert not checks[-1].ok


def test_verify_stops_when_the_namespace_cannot_be_resolved() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("ctx")), \
         patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="c")), \
         patch("factory.contained.k8s_setup.login_status", return_value=(True, "me")), \
         patch("factory.contained.k8s_setup.resolve_namespace",
               side_effect=ClusterError("no namespace")):
        checks = k8s_setup.verify_k8s(namespace=None)
    assert checks[-1].name == "namespace" and not checks[-1].ok


def test_verify_runs_the_division_check_when_asked() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("ok")), \
         patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="c")), \
         patch("factory.contained.k8s_setup.login_status", return_value=(True, "me")), \
         patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup.secret_check",
               return_value=Check("credentials_secret", False, "missing", fix="x")), \
         patch("factory.contained.k8s_setup.build_api_resources_argv", return_value=["oc"]):
        checks = k8s_setup.verify_k8s(namespace="ns", division=True, probe_inference=False)
    assert any(c.name == "build_api" for c in checks)


# ---------------------------------------------------------------------------------------------
# _login_check
# ---------------------------------------------------------------------------------------------


def test_login_check_authenticated() -> None:
    with patch("factory.contained.k8s_setup.login_status", return_value=(True, "me@example.com")):
        check = k8s_setup._login_check("oc")
    assert check.ok and "me@example.com" in check.detail


def test_login_check_expired_session_reads_as_a_login_problem() -> None:
    with patch("factory.contained.k8s_setup.login_status",
               return_value=(False, "You must be logged in (Unauthorized)")):
        check = k8s_setup._login_check("oc")
    assert not check.ok and "expired" in check.detail


def test_login_check_other_failure_reports_the_detail() -> None:
    with patch("factory.contained.k8s_setup.login_status",
               return_value=(False, "connection refused")):
        check = k8s_setup._login_check("oc")
    assert not check.ok and "connection refused" in check.detail


# ---------------------------------------------------------------------------------------------
# _object_checks — a failed read is not absence
# ---------------------------------------------------------------------------------------------


def test_object_check_present() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("serviceaccount/factory")):
        checks = k8s_setup._object_checks("oc", "ns", division=False)
    assert all(c.ok for c in checks)


def test_object_check_genuinely_missing_points_at_the_bundle() -> None:
    not_found = _completed(returncode=1, stderr='Error (NotFound): serviceaccounts "x" not found')
    with patch("factory.contained.k8s_setup._run", return_value=not_found):
        checks = k8s_setup._object_checks("oc", "ns", division=False)
    assert not checks[0].ok and "is missing" in checks[0].detail
    assert "bundle |" in (checks[0].fix or "")


def test_object_check_auth_error_is_unknown_and_says_log_in() -> None:
    with patch("factory.contained.k8s_setup._run",
               return_value=_completed(returncode=1, stderr="error: Unauthorized")):
        checks = k8s_setup._object_checks("oc", "ns", division=False)
    assert not checks[0].ok and "could not be checked" in checks[0].detail
    assert "login" in (checks[0].fix or "")


def test_object_check_other_error_carries_its_reason() -> None:
    with patch("factory.contained.k8s_setup._run",
               return_value=_completed(returncode=1, stderr="Error (Forbidden): nope")):
        checks = k8s_setup._object_checks("oc", "ns", division=False)
    assert not checks[0].ok and "Forbidden" in checks[0].detail


def test_object_check_unreadable_when_the_cli_could_not_run() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=None):
        checks = k8s_setup._object_checks("oc", "ns", division=False)
    assert not checks[0].ok and "could not be checked" in checks[0].detail


# ---------------------------------------------------------------------------------------------
# _inference_result and the probe
# ---------------------------------------------------------------------------------------------


def test_inference_result_skips_the_probe_without_a_secret() -> None:
    secret = Check("credentials_secret", False, "missing", fix="make it")
    check = k8s_setup._inference_result("oc", "ns", secret, announce=False)
    assert not check.ok and "not attempted" in check.detail and check.fix == "make it"


def test_inference_result_announces_then_probes() -> None:
    secret = Check("credentials_secret", True, "present")
    with patch("factory.contained.k8s_setup._inference_check",
               return_value=Check("inference_from_cluster", True, "reached")) as probe:
        check = k8s_setup._inference_result("oc", "ns", secret, announce=True)
    probe.assert_called_once()
    assert check.ok


def _probe_run(**verdicts):
    """A subprocess.run stand-in for the probe: delete/apply/logs keyed off argv."""
    def run(argv, **kw):
        if "apply" in argv:
            return _completed(verdicts.get("apply_out", "created"),
                              returncode=verdicts.get("apply_rc", 0),
                              stderr=verdicts.get("apply_err", ""))
        if "logs" in argv:
            return _completed(verdicts.get("logs", ""))
        return _completed("")                    # delete, cleanup
    return run


def test_probe_reports_success_when_the_pod_reaches_inference() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=_probe_run(logs="PROBE_OK")), \
         patch("factory.contained.k8s_setup.poll_pod",
               return_value=PodProgress(SUCCEEDED, "Succeeded", "Succeeded", "")):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert check.ok


def test_probe_reports_a_pod_that_could_not_be_created() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run",
               side_effect=_probe_run(apply_rc=1, apply_err="quota exceeded")):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert not check.ok and "could not be created" in check.detail


def test_probe_reports_a_doomed_pod_with_the_kubelet_reason() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=_probe_run(logs="")), \
         patch("factory.contained.k8s_setup.poll_pod",
               return_value=PodProgress(DOOMED, "Pending", "ImagePullBackOff", "no such image")):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert not check.ok and "ImagePullBackOff" in check.detail


def test_probe_reports_the_pods_last_line_when_it_ran_but_failed() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run",
               side_effect=_probe_run(logs="probing...\nno response — DNS")), \
         patch("factory.contained.k8s_setup.poll_pod",
               return_value=PodProgress(SUCCEEDED, "Succeeded", "Succeeded", "")):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert not check.ok and "no response" in check.detail


def test_probe_reports_no_output_when_neither_logs_nor_a_doomed_reason() -> None:
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=_probe_run(logs="")), \
         patch("factory.contained.k8s_setup.poll_pod",
               return_value=PodProgress(WAITING, "P", "P", "")):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert not check.ok and "no output" in check.detail


def test_probe_survives_a_subprocess_failure() -> None:
    """An error mid-probe is caught; the `finally` cleanup still runs, so it must not itself raise."""
    def run(argv, **kw):
        if "apply" in argv:
            raise OSError("no oc")
        return _completed("")                    # delete before, and the finally cleanup after

    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=run):
        check = k8s_setup._inference_check("oc", "ns", "img")
    assert not check.ok and "could not be run" in check.detail


def test_probe_manifest_names_the_pod_and_namespace() -> None:
    manifest = k8s_setup._probe_pod_manifest("probe-x", "ns", "img:1")
    assert "probe-x" in manifest and "ns" in manifest and "img:1" in manifest


# ---------------------------------------------------------------------------------------------
# _division_checks
# ---------------------------------------------------------------------------------------------


def test_division_present_and_absent() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("builds\n")):
        assert k8s_setup._division_checks("ns")[0].ok
    with patch("factory.contained.k8s_setup._run", return_value=_completed("")):
        assert not k8s_setup._division_checks("ns")[0].ok


# ---------------------------------------------------------------------------------------------
# _apply_object
# ---------------------------------------------------------------------------------------------


def test_apply_object_success_failure_and_exception() -> None:
    from factory.contained.bundle import BundleObject

    obj = BundleObject(kind="role", name="factory", purpose="p", manifest="kind: Role\n")
    with patch("factory.contained.k8s_setup.subprocess.run", return_value=_completed("configured")):
        assert k8s_setup._apply_object(obj, "ns", "oc") == (True, "configured")
    with patch("factory.contained.k8s_setup.subprocess.run",
               return_value=_completed(returncode=1, stderr="forbidden")):
        ok, detail = k8s_setup._apply_object(obj, "ns", "oc")
        assert not ok and "forbidden" in detail
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=OSError("gone")):
        ok, detail = k8s_setup._apply_object(obj, "ns", "oc")
        assert not ok and "OSError" in detail


# ---------------------------------------------------------------------------------------------
# setup_k8s — the top-level flow
# ---------------------------------------------------------------------------------------------


def test_setup_returns_2_when_no_cli() -> None:
    with patch("factory.contained.k8s_setup.cli_binary", side_effect=ClusterError("none")):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True) == 2


def test_setup_aborts_when_the_context_chooser_is_escaped() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value=k8s_setup._ABORT):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True) == 1


def test_setup_returns_2_when_the_namespace_lookup_errors() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value="ctx"), \
         patch("factory.contained.k8s_setup._choose_namespace",
               side_effect=ClusterError("boom")):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True) == 2


def test_setup_aborts_when_no_namespace_is_chosen() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value=None), \
         patch("factory.contained.k8s_setup._choose_namespace", return_value=None):
        assert k8s_setup.setup_k8s(namespace=None, division=False, interactive=True) == 1


def test_setup_stops_when_not_logged_in() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value=None), \
         patch("factory.contained.k8s_setup._choose_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup.login_status", return_value=(False, "Unauthorized")):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True) == 1


def test_setup_non_interactive_without_yes_applies_nothing() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value=None), \
         patch("factory.contained.k8s_setup._choose_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup.login_status", return_value=(True, "me")), \
         patch("factory.contained.k8s_setup.inspect_objects", return_value=[]):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=False) == 1


def _walk_result(*, failed=False, aborted=False):
    class _R:
        pass
    r = _R()
    r.failed, r.aborted = failed, aborted
    return r


def test_setup_walk_aborted_names_the_verify_command() -> None:
    with patch("factory.contained.k8s_setup._choose_context", return_value=None), \
         patch("factory.contained.k8s_setup._choose_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup.login_status", return_value=(True, "me")), \
         patch("factory.contained.k8s_setup.inspect_objects", return_value=[]), \
         patch("factory.contained.k8s_setup.walk", return_value=_walk_result(aborted=True)):
        assert k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True) == 1


def test_setup_full_success_runs_credentials_then_verify() -> None:
    ok = [Check("cluster_cli", True, "x")]
    with patch("factory.contained.k8s_setup._choose_context", return_value=None), \
         patch("factory.contained.k8s_setup._choose_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup.login_status", return_value=(True, "me")), \
         patch("factory.contained.k8s_setup.inspect_objects", return_value=[]), \
         patch("factory.contained.k8s_setup.walk",
               return_value=_walk_result(failed=True)) as walked, \
         patch("factory.contained.k8s_setup.run_credentials_step", return_value=True) as creds, \
         patch("factory.contained.k8s_setup.verify_k8s", return_value=ok):
        code = k8s_setup.setup_k8s(namespace="ns", division=False, interactive=True)
    walked.assert_called_once()
    creds.assert_called_once()
    assert code == 0


# ---------------------------------------------------------------------------------------------
# _finish, and the default-context switch offer
# ---------------------------------------------------------------------------------------------


def test_finish_offers_the_default_switch_when_a_context_was_pinned() -> None:
    k8s_setup.set_active_context("prepared-ctx")
    with patch("factory.contained.k8s_setup.run_credentials_step", return_value=True), \
         patch("factory.contained.k8s_setup.verify_k8s",
               return_value=[Check("x", True, "ok")]), \
         patch("factory.contained.k8s_setup._offer_default_switch") as offer:
        code = k8s_setup._finish("oc", "ns", division=False, interactive=True)
    offer.assert_called_once()
    assert code == 0


# ---------------------------------------------------------------------------------------------
# _choose_context / _ask_context
# ---------------------------------------------------------------------------------------------


def test_choose_context_returns_none_with_fewer_than_two() -> None:
    with patch("factory.contained.k8s_setup.list_contexts",
               return_value=[ClusterContext(context="only")]):
        assert k8s_setup._choose_context(interactive=True) is None


def test_ask_context_accepts_a_number_a_name_and_reprompts_on_junk() -> None:
    ctxs = [ClusterContext(context="a", server="s1"), ClusterContext(context="b", server="s2")]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=ctxs), \
         patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="a")), \
         patch("factory.contained.k8s_setup.style.read_line", side_effect=["9", "junk", "b"]):
        assert k8s_setup._choose_context(interactive=True) == "b"


def test_ask_context_aborts_on_escape() -> None:
    ctxs = [ClusterContext(context="a"), ClusterContext(context="b")]
    with patch("factory.contained.k8s_setup.list_contexts", return_value=ctxs), \
         patch("factory.contained.k8s_setup.style.read_line", return_value=None):
        assert k8s_setup._choose_context(interactive=True) is k8s_setup._ABORT


# ---------------------------------------------------------------------------------------------
# _offer_default_switch
# ---------------------------------------------------------------------------------------------


def test_offer_switch_is_a_noop_when_already_current() -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="c")):
        k8s_setup._offer_default_switch("c", interactive=True)   # returns early, nothing raised


def test_offer_switch_non_interactive_just_prints_the_command() -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="other")):
        k8s_setup._offer_default_switch("c", interactive=False)


def test_offer_switch_declined() -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="other")), \
         patch("factory.contained.k8s_setup.style.confirm", return_value=False):
        k8s_setup._offer_default_switch("c", interactive=True)


def test_offer_switch_accepted_success_and_failure() -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="other")), \
         patch("factory.contained.k8s_setup.style.confirm", return_value=True), \
         patch("factory.contained.k8s_setup.use_context", return_value=(True, "now c")):
        k8s_setup._offer_default_switch("c", interactive=True)
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="other")), \
         patch("factory.contained.k8s_setup.style.confirm", return_value=True), \
         patch("factory.contained.k8s_setup.use_context", return_value=(False, "denied")):
        k8s_setup._offer_default_switch("c", interactive=True)


# ---------------------------------------------------------------------------------------------
# _print_context field variants
# ---------------------------------------------------------------------------------------------


def test_print_context_full_and_empty() -> None:
    with patch("factory.contained.k8s_setup.cluster_context",
               return_value=ClusterContext(context="c", server="s", user="u")):
        k8s_setup._print_context("ns")
    with patch("factory.contained.k8s_setup.cluster_context", return_value=ClusterContext()):
        k8s_setup._print_context(None)


# ---------------------------------------------------------------------------------------------
# _namespace_status / _create_namespace / _resolve_existing / _choose_namespace
# ---------------------------------------------------------------------------------------------


def test_namespace_status_present_absent_unreadable() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("namespace/ns")):
        assert k8s_setup._namespace_status("ns", "oc") == k8s_setup.PRESENT
    with patch("factory.contained.k8s_setup._run", return_value=None):
        assert k8s_setup._namespace_status("ns", "kubectl") == k8s_setup.UNREADABLE
    with patch("factory.contained.k8s_setup._run",
               return_value=_completed(returncode=1, stderr="Error (NotFound): not found")):
        assert k8s_setup._namespace_status("ns", "kubectl") == k8s_setup.ABSENT
    # oc falls back to `get project`; a Forbidden on both is unreadable, not absent.
    with patch("factory.contained.k8s_setup._run",
               return_value=_completed(returncode=1, stderr="Forbidden")):
        assert k8s_setup._namespace_status("ns", "oc") == k8s_setup.UNREADABLE


def test_create_namespace_success_failure_and_unrunnable() -> None:
    with patch("factory.contained.k8s_setup._run", return_value=_completed("created")):
        assert k8s_setup._create_namespace("ns", "oc")[0] is True
    with patch("factory.contained.k8s_setup._run", return_value=None):
        ok, detail = k8s_setup._create_namespace("ns", "kubectl")
        assert not ok and "could not run" in detail
    with patch("factory.contained.k8s_setup._run",
               return_value=_completed(returncode=1, stderr="denied")):
        ok, detail = k8s_setup._create_namespace("ns", "oc")
        assert not ok and "denied" in detail


def test_resolve_existing_present_and_unreadable_are_ok() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.PRESENT):
        assert k8s_setup._resolve_existing("ns", "oc", interactive=True, assume_yes=False) == "ok"
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.UNREADABLE):
        assert k8s_setup._resolve_existing("ns", "oc", interactive=True, assume_yes=False) == "ok"


def test_resolve_existing_absent_non_interactive_aborts() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT):
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=False, assume_yes=False) == "abort"


def test_resolve_existing_absent_declined_retries_and_escaped_aborts() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup.style.confirm", return_value=False):
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=True, assume_yes=False) == "retry"
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup.style.confirm", return_value=None):
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=True, assume_yes=False) == "abort"


def test_resolve_existing_absent_creates_when_confirmed() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace", return_value=(True, "made")):
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=True, assume_yes=True) == "ok"


def test_resolve_existing_create_failure_aborts_or_retries() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace", return_value=(False, "denied")):
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=False, assume_yes=True) == "abort"
        assert k8s_setup._resolve_existing(
            "ns", "oc", interactive=True, assume_yes=True) == "retry"


def test_choose_namespace_explicit_ok_and_not_ok() -> None:
    with patch("factory.contained.k8s_setup._resolve_existing", return_value="ok"):
        assert k8s_setup._choose_namespace(
            "ns", interactive=True, binary="oc") == "ns"
    with patch("factory.contained.k8s_setup._resolve_existing", return_value="abort"):
        assert k8s_setup._choose_namespace(
            "ns", interactive=True, binary="oc") is None


def test_choose_namespace_non_interactive_uses_the_current_context() -> None:
    with patch("factory.contained.k8s_setup.resolve_namespace", return_value="ns"), \
         patch("factory.contained.k8s_setup._resolve_existing", return_value="ok"):
        assert k8s_setup._choose_namespace(
            None, interactive=False, binary="oc") == "ns"


def test_choose_namespace_interactive_reprompts_on_empty_then_accepts() -> None:
    with patch("factory.contained.k8s_setup.current_namespace", return_value=None), \
         patch("factory.contained.k8s_setup.style.read_line", side_effect=["", "ns"]), \
         patch("factory.contained.k8s_setup._resolve_existing", return_value="ok"):
        assert k8s_setup._choose_namespace(
            None, interactive=True, binary="oc") == "ns"


def test_choose_namespace_interactive_escape_and_abort() -> None:
    with patch("factory.contained.k8s_setup.current_namespace", return_value="cur"), \
         patch("factory.contained.k8s_setup.style.read_line", return_value=None):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") is None
    with patch("factory.contained.k8s_setup.current_namespace", return_value="cur"), \
         patch("factory.contained.k8s_setup.style.read_line", return_value="ns"), \
         patch("factory.contained.k8s_setup._resolve_existing", return_value="abort"):
        assert k8s_setup._choose_namespace(None, interactive=True, binary="oc") is None


# ---------------------------------------------------------------------------------------------
# _verb_checks — unknown and denied
# ---------------------------------------------------------------------------------------------


def test_verb_checks_unknown_when_the_review_cannot_run() -> None:
    with patch("factory.contained.k8s_setup.access_review", return_value=None):
        checks = k8s_setup._verb_checks("ns", division=False)
    perms = next(c for c in checks if c.name == "permissions")
    assert not perms.ok and "unknown" in perms.detail


def test_verb_checks_names_the_denied_verbs() -> None:
    with patch("factory.contained.k8s_setup.access_review", return_value=False):
        checks = k8s_setup._verb_checks("ns", division=False)
    perms = next(c for c in checks if c.name == "permissions")
    assert not perms.ok and "cannot" in perms.detail


def test_probe_updates_the_status_line_when_given_one() -> None:
    """With an Activity attached, `say()` drives it; without one it is a no-op (other tests)."""
    import io

    act = k8s_setup.style.Activity("probe", stream=io.StringIO(), threshold=0.0)
    with patch("factory.contained.k8s_setup.subprocess.run", side_effect=_probe_run(logs="PROBE_OK")), \
         patch("factory.contained.k8s_setup.poll_pod",
               return_value=PodProgress(SUCCEEDED, "Succeeded", "Succeeded", "")):
        check = k8s_setup._inference_check("oc", "ns", "img", act=act)
    assert check.ok


def test_resolve_existing_create_on_plain_kubernetes_skips_the_oc_note() -> None:
    with patch("factory.contained.k8s_setup._namespace_status", return_value=k8s_setup.ABSENT), \
         patch("factory.contained.k8s_setup._create_namespace", return_value=(True, "made")):
        assert k8s_setup._resolve_existing(
            "ns", "kubectl", interactive=True, assume_yes=True) == "ok"
