"""Small cluster-side helpers whose failure directions are otherwise unexercised.

The interactive walk's keypress branches matter more than their size suggests: Escape and Enter are
the two keys a user presses when they want *out*, and reading either as "apply" would apply RBAC to
a cluster the user had already decided against.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from unittest.mock import patch

from factory.contained import k8s_division, k8s_review, style
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
# The two rendering helpers
# --------------------------------------------------------------------------------------------


def test_the_registration_is_stable_json_so_two_renderings_compare() -> None:
    payload = k8s_division.registration_json("ns")
    assert json.loads(payload) == k8s_division.mcp_config("ns")
    assert payload == json.dumps(json.loads(payload), sort_keys=True)


def test_the_sidecar_command_is_quoted_for_embedding_in_another_command_line() -> None:
    """It is spliced into a shell line; unquoted, its own newlines end the command early."""
    assert k8s_division.quoted_sidecar_command() == shlex.quote(k8s_division.sidecar_command())


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
    """"Unknown" prompts the user; "current" silently skips an object the cluster may not have."""
    from factory.contained.bundle import BundleObject

    obj = BundleObject(
        kind="role", name="factory", purpose="lets the run manage its own pod",
        manifest="kind: Role\n",
    )
    with patch("factory.contained.k8s_review._run", side_effect=[
        subprocess.CompletedProcess([], 0, "", ""),   # `get` — the object exists
        None,                                         # `diff` — could not run
    ]):
        state = k8s_review._inspect_one(obj, "ns", "oc")
    assert state.status == k8s_review.UNKNOWN
