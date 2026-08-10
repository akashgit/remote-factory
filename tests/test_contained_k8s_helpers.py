"""Small cluster-side helpers whose failure directions are otherwise unexercised.

The interactive walk's keypress branches matter more than their size suggests: Escape and Enter are
the two keys a user presses when they want *out*, and reading either as "apply" would apply RBAC to
a cluster the user had already decided against.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from factory.contained import k8s, k8s_review, style
from factory.contained.k8s_division import openshift_available


# --------------------------------------------------------------------------------------------
# Detecting the OpenShift Build API
# --------------------------------------------------------------------------------------------


def test_a_cluster_serving_builds_is_available() -> None:
    result = subprocess.CompletedProcess([], 0, "builds  build.openshift.io/v1  Build", "")
    assert openshift_available(runner=lambda argv: result) is True


def test_a_cluster_that_answers_without_builds_is_not_available() -> None:
    """Detected by API presence, not by the `oc` binary: `oc` against a vanilla cluster works fine
    for everything except the one thing the division needs."""
    result = subprocess.CompletedProcess([], 0, "", "")
    assert openshift_available(runner=lambda argv: result) is False


def test_an_unreachable_cluster_is_not_available_rather_than_an_exception() -> None:
    """This runs at launch, before anything is provisioned; a traceback there names nothing."""

    def _raise(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("oc")

    assert openshift_available(runner=_raise) is False


def test_a_cluster_query_that_times_out_is_not_available() -> None:
    def _raise(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="oc", timeout=60)

    assert openshift_available(runner=_raise) is False


# --------------------------------------------------------------------------------------------
# The review walk's keypress handling
# --------------------------------------------------------------------------------------------


def test_escape_stops_the_walk_without_applying_anything() -> None:
    """Escape is what a user presses to get out. Reading it as anything else applies RBAC they had
    just decided against."""
    with patch.object(style, "read_key", return_value=style.ESCAPE):
        assert k8s_review._ask(1, 3) == "q"


def test_enter_skips_this_object_rather_than_applying_it() -> None:
    """The prompt says "Enter = skip", and the safe default for an apply is not to."""
    with patch.object(style, "read_key", return_value="\r"):
        assert k8s_review._ask(1, 3) == "n"


def test_an_arrow_key_is_ignored_and_the_question_is_asked_again() -> None:
    """An escape *sequence* arrives as an empty read; treating it as an answer would apply or skip
    on a cursor key."""
    with patch.object(style, "read_key", side_effect=["", "y"]):
        assert k8s_review._ask(1, 3) == "y"


def test_an_unrecognised_key_shows_the_options_rather_than_choosing_one() -> None:
    with patch.object(style, "read_key", side_effect=["z", "a"]):
        assert k8s_review._ask(1, 3) == "a"


def test_a_diff_that_cannot_be_run_is_reported_as_unknown_not_as_current() -> None:
    """ "Unknown" prompts the user; "current" silently skips an object the cluster may not have."""
    from factory.contained.bundle import BundleObject

    obj = BundleObject(
        kind="role",
        name="factory",
        purpose="lets the run manage its own pod",
        manifest="kind: Role\n",
    )
    with patch(
        "factory.contained.k8s_review._run",
        side_effect=[
            subprocess.CompletedProcess([], 0, "", ""),  # `get` — the object exists
            None,  # `diff` — could not run
        ],
    ):
        state = k8s_review._inspect_one(obj, "ns", "oc")
    assert state.status == k8s_review.UNKNOWN


# --------------------------------------------------------------------------------------------
# Reading a pod's state well enough to stop waiting on a hopeless one
# --------------------------------------------------------------------------------------------


def _pod(*, phase: str = "Pending", waiting: dict | None = None, running: bool = False,
         terminated: dict | None = None, conditions: list | None = None,
         name: str = "probe") -> dict:
    state: dict = {}
    if waiting is not None:
        state["waiting"] = waiting
    if running:
        state["running"] = {"startedAt": "now"}
    if terminated is not None:
        state["terminated"] = terminated
    status: dict = {"phase": phase}
    if state:
        status["containerStatuses"] = [{"name": name, "state": state}]
    if conditions is not None:
        status["conditions"] = conditions
    return {"status": status}


def test_an_unpullable_image_is_doomed_immediately_not_after_the_timeout() -> None:
    """The defect this exists for: three minutes of silence, then "the probe produced no output".

    `ImagePullBackOff` is the kubelet saying it has already retried and given up. Waiting past it
    buys nothing, and the message it carries is the answer the user actually needs.
    """
    progress = k8s.classify_pod(_pod(waiting={
        "reason": "ImagePullBackOff",
        "message": 'Back-off pulling image "ghcr.io/akashgit/remote-factory/factory-runtime"',
    }))
    assert progress.verdict == k8s.DOOMED
    assert progress.reason == "ImagePullBackOff"
    assert "Back-off pulling image" in progress.describe()


def test_a_secret_missing_a_key_is_doomed_immediately() -> None:
    progress = k8s.classify_pod(_pod(waiting={
        "reason": "CreateContainerConfigError", "message": "secret 'factory-credentials' not found",
    }))
    assert progress.verdict == k8s.DOOMED
    assert "factory-credentials" in progress.describe()


def test_a_first_pull_is_not_mistaken_for_a_failure() -> None:
    """A cold `ContainerCreating` legitimately runs for minutes; capping it would break every
    first run on a fresh node."""
    progress = k8s.classify_pod(_pod(waiting={"reason": "ContainerCreating", "message": ""}))
    assert progress.verdict == k8s.WAITING
    assert progress.reason == "ContainerCreating"


def test_a_retryable_pull_error_is_not_doomed_on_sight() -> None:
    """`ErrImagePull` is the attempt; `ImagePullBackOff` is the verdict. Only the second is final."""
    progress = k8s.classify_pod(_pod(waiting={"reason": "ErrImagePull", "message": "timeout"}))
    assert progress.verdict == k8s.WAITING
    assert progress.reason in k8s.RETRYABLE_WAITING_REASONS


def test_a_pod_no_node_will_accept_is_doomed_with_the_schedulers_words() -> None:
    """It sits in Pending with no container status at all, which reads as "starting"."""
    progress = k8s.classify_pod(_pod(conditions=[{
        "type": "PodScheduled", "status": "False", "reason": "Unschedulable",
        "message": "0/6 nodes are available: insufficient memory",
    }]))
    assert progress.verdict == k8s.DOOMED
    assert "insufficient memory" in progress.describe()


def test_a_running_container_is_running_and_a_clean_exit_succeeded() -> None:
    assert k8s.classify_pod(_pod(running=True)).verdict == k8s.RUNNING
    done = k8s.classify_pod(_pod(phase="Succeeded", terminated={"exitCode": 0,
                                                               "reason": "Completed"}))
    assert done.verdict == k8s.SUCCEEDED


def test_a_nonzero_exit_is_doomed_and_carries_its_code() -> None:
    progress = k8s.classify_pod(_pod(phase="Failed", terminated={"exitCode": 7, "reason": "Error"}))
    assert progress.verdict == k8s.DOOMED
    assert "7" in progress.describe()


def test_one_container_can_be_asked_about_by_name() -> None:
    """The loader's window is "that initContainer is running", which no pod condition expresses."""
    pod = {"status": {"phase": "Pending", "initContainerStatuses": [
        {"name": "workspace-loader", "state": {"running": {}}},
    ], "containerStatuses": [
        {"name": "factory", "state": {"waiting": {"reason": "PodInitializing"}}},
    ]}}
    assert k8s.classify_pod(pod, container="workspace-loader").verdict == k8s.RUNNING
    assert k8s.classify_pod(pod, container="factory").verdict == k8s.WAITING


def test_an_unrecognized_state_waits_rather_than_giving_up() -> None:
    """Being wrong in this direction aborts a run over a state that would have cleared."""
    progress = k8s.classify_pod(_pod(waiting={"reason": "SomethingNewInKubernetes"}))
    assert progress.verdict == k8s.WAITING


def test_an_empty_or_malformed_pod_never_raises() -> None:
    for payload in ({}, {"status": None}, {"status": {"containerStatuses": None}}):
        assert k8s.classify_pod(payload).verdict in (k8s.WAITING, k8s.DOOMED)


def test_polling_stops_the_moment_a_pod_is_doomed() -> None:
    """Not after the timeout: the first poll already knew, and the user waited three minutes."""
    doomed = _pod(waiting={"reason": "ImagePullBackOff", "message": "no such image"})
    with patch("factory.contained.k8s.read_pod", return_value=doomed), \
         patch("factory.contained.k8s.time.sleep") as slept:
        progress = k8s.poll_pod("probe", "ns", timeout=180)
    assert progress.verdict == k8s.DOOMED
    slept.assert_not_called()


def test_polling_reports_each_change_once() -> None:
    states = [
        _pod(waiting={"reason": "ContainerCreating"}),
        _pod(waiting={"reason": "ContainerCreating"}),
        _pod(running=True),
    ]
    seen: list[str] = []
    with patch("factory.contained.k8s.read_pod", side_effect=states), \
         patch("factory.contained.k8s.time.sleep"):
        k8s.poll_pod("probe", "ns", timeout=180, on_progress=lambda p: seen.append(p.reason))
    assert seen == ["ContainerCreating", "Running"]


def test_a_wait_that_times_out_says_what_it_was_still_waiting_for() -> None:
    with patch("factory.contained.k8s.read_pod",
               return_value=_pod(waiting={"reason": "ContainerCreating"})), \
         patch("factory.contained.k8s.time.sleep"):
        progress = k8s.poll_pod("probe", "ns", timeout=0)
    assert progress.verdict == k8s.DOOMED
    assert progress.reason == "Timeout"


def test_wait_for_container_names_the_reason_rather_than_reporting_a_timeout() -> None:
    """It used to spend its full five minutes and then blame the clock."""
    with patch("factory.contained.k8s.read_pod", return_value=_pod(
        waiting={"reason": "ImagePullBackOff", "message": "manifest unknown"}, name="factory")), \
         patch("factory.contained.k8s.time.sleep"), \
         patch("factory.contained.k8s.cli_binary", return_value="oc"):
        with pytest.raises(k8s.ClusterError) as raised:
            k8s.wait_for_container("pod", "ns", "factory", timeout=300)
    assert "ImagePullBackOff" in str(raised.value)
    assert "manifest unknown" in str(raised.value)


# --------------------------------------------------------------------------------------------
# A failed read is not evidence of absence
# --------------------------------------------------------------------------------------------

_UNAUTHORIZED = (
    'error: You must be logged in to the server (Unauthorized)\n'
    'couldn\'t get current server API group list: the server has asked for the client to '
    'provide credentials'
)


def _obj():
    from factory.contained.bundle import BundleObject

    return BundleObject(kind="serviceaccount", name="factory",
                        purpose="the identity the pod runs as", manifest="kind: ServiceAccount\n")


def test_an_expired_login_is_not_reported_as_a_missing_object() -> None:
    """The defect: a fully prepared namespace read as an empty one.

    Every `oc get` failed with Unauthorized, every failure was classified as "not there", and the
    review offered to create five objects that already existed — directly contradicting the honest
    "could not confirm whether the namespace exists" printed one line above.
    """
    with patch("factory.contained.k8s_review._run",
               return_value=subprocess.CompletedProcess([], 1, "", _UNAUTHORIZED)):
        state = k8s_review._inspect_one(_obj(), "factory-yi", "oc")
    assert state.status == k8s_review.UNKNOWN
    assert "not logged in" in state.detail


def test_a_genuine_notfound_is_still_absent() -> None:
    """The distinction has to cut both ways, or a first setup stops offering to create anything."""
    stderr = 'Error from server (NotFound): serviceaccounts "factory" not found'
    with patch("factory.contained.k8s_review._run",
               return_value=subprocess.CompletedProcess([], 1, "", stderr)):
        state = k8s_review._inspect_one(_obj(), "factory-yi", "oc")
    assert state.status == k8s_review.ABSENT


def test_any_other_read_failure_is_unknown_and_carries_its_reason() -> None:
    stderr = "Error from server (Forbidden): serviceaccounts is forbidden"
    with patch("factory.contained.k8s_review._run",
               return_value=subprocess.CompletedProcess([], 1, "", stderr)):
        state = k8s_review._inspect_one(_obj(), "factory-yi", "oc")
    assert state.status == k8s_review.UNKNOWN
    assert "Forbidden" in state.detail


def test_an_auth_error_is_recognized_however_the_cli_words_it() -> None:
    for text in (
        "error: You must be logged in to the server (Unauthorized)",
        "the server has asked for the client to provide credentials",
        "Unauthorized",
        "invalid bearer token",
    ):
        assert k8s.is_auth_error(text), text
    assert not k8s.is_auth_error('serviceaccounts "factory" not found')


def test_a_login_check_asks_the_cluster_not_the_kubeconfig() -> None:
    """`config current-context` reads a local file and passes with an hours-dead token."""
    with patch("factory.contained.k8s._run",
               return_value=subprocess.CompletedProcess([], 1, "", _UNAUTHORIZED)):
        ok, detail = k8s.login_status("oc")
    assert ok is False
    assert "logged in" in detail.lower() or "credentials" in detail.lower()

    with patch("factory.contained.k8s._run",
               return_value=subprocess.CompletedProcess([], 0, "yizheng@redhat.com\n", "")):
        ok, detail = k8s.login_status("oc")
    assert ok is True and detail == "yizheng@redhat.com"


def test_the_login_probe_is_an_authenticated_round_trip() -> None:
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess([], 0, "you", "")

    with patch("factory.contained.k8s._run", side_effect=fake_run):
        k8s.login_status("oc")
    assert "whoami" in seen["argv"]
    with patch("factory.contained.k8s._run", side_effect=fake_run):
        k8s.login_status("kubectl")
    assert "auth" in seen["argv"] and "can-i" in seen["argv"]
