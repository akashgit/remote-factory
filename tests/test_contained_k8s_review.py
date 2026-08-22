"""The object-by-object review: what state each object is in, and what the walk does about it."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from factory.contained import k8s_review
from factory.contained.bundle import BundleObject, bundle_objects, render_bundle
from factory.contained.k8s_review import (
    ABSENT,
    CURRENT,
    DIFFERS,
    UNKNOWN,
    ObjectState,
    inspect_objects,
    render_summary,
    walk,
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _obj(name: str = "factory") -> BundleObject:
    return BundleObject(kind="serviceaccount", name=name, purpose="why it exists",
                        manifest="kind: ServiceAccount\n")


def _state(status: str, name: str = "factory", diff: str = "") -> ObjectState:
    return ObjectState(_obj(name), status, diff=diff, detail=status)


# ---------------------------------------------------------------------------------------------
# The bundle as a list, and as a blob
# ---------------------------------------------------------------------------------------------


def test_the_blob_and_the_list_describe_the_same_objects() -> None:
    """`bundle` prints one and `setup` walks the other; they cannot be allowed to drift."""
    import yaml

    objects = bundle_objects(namespace="ns")
    docs = [d for d in yaml.safe_load_all(render_bundle(namespace="ns")) if d]
    assert len(objects) == len(docs)
    assert [o.name for o in objects] == [d["metadata"]["name"] for d in docs]


def test_every_object_explains_itself() -> None:
    """A prompt asking to allow something into your namespace has to say what it is for."""
    for obj in bundle_objects(namespace="ns", division=True):
        assert len(obj.purpose) > 40, f"{obj.ref} has no usable explanation"


# ---------------------------------------------------------------------------------------------
# Establishing the current state
# ---------------------------------------------------------------------------------------------


def test_a_missing_object_is_absent_and_never_diffed() -> None:
    """`NotFound` on stderr is what makes it absent — not merely a non-zero exit.

    Any non-zero used to qualify, so an expired login turned a prepared namespace into an empty
    one. The `get` has to actually say the object is not there.
    """
    not_found = _completed("", 1)
    not_found.stderr = 'Error from server (NotFound): serviceaccounts "factory" not found'
    with patch("factory.contained.k8s_review._run", return_value=not_found) as run:
        states = inspect_objects([_obj()], "ns", "oc")
    assert states[0].status == ABSENT
    # One call: `get`. Diffing something that does not exist wastes a round trip per object.
    assert run.call_count == 1


def test_an_object_that_matches_is_current() -> None:
    with patch("factory.contained.k8s_review._run",
               side_effect=[_completed("serviceaccount/factory"), _completed("", 0)]):
        states = inspect_objects([_obj()], "ns", "oc")
    assert states[0].status == CURRENT
    assert not states[0].needs_action


def test_an_object_that_differs_carries_its_diff() -> None:
    with patch("factory.contained.k8s_review._run",
               side_effect=[_completed("serviceaccount/factory"),
                            _completed("-  verbs: [get]\n+  verbs: [get, list]\n", 1)]):
        states = inspect_objects([_obj()], "ns", "oc")
    assert states[0].status == DIFFERS
    assert "verbs: [get, list]" in states[0].diff
    assert states[0].needs_action


def test_a_diff_that_failed_is_unknown_not_current() -> None:
    """Exit 1 with nothing on stdout is a failure, and reading it as "no change" hides an object."""
    with patch("factory.contained.k8s_review._run",
               side_effect=[_completed("serviceaccount/factory"),
                            _completed("", 1, stderr="error: forbidden")]):
        states = inspect_objects([_obj()], "ns", "oc")
    assert states[0].status == UNKNOWN
    assert states[0].needs_action        # unknown is never silently skipped


def test_an_unreachable_cluster_is_unknown_not_a_crash() -> None:
    with patch("factory.contained.k8s_review._run", return_value=None):
        states = inspect_objects([_obj()], "ns", "oc")
    assert states[0].status == UNKNOWN


# ---------------------------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------------------------


def test_the_summary_counts_what_is_already_correct() -> None:
    states = [_state(CURRENT, "a"), _state(CURRENT, "b"), _state(ABSENT, "c")]
    rendered = render_summary(states, "ns")
    assert "2 already correct" in rendered
    # Every object appears, including the settled ones.
    for name in ("a", "b", "c"):
        assert f"serviceaccount/{name}" in rendered


def test_a_namespace_that_needs_nothing_says_so() -> None:
    rendered = render_summary([_state(CURRENT)], "ns")
    assert "already in place" in rendered
    assert "decision" not in rendered


# ---------------------------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------------------------


def _recorder(fail: set[str] | None = None):
    """A stand-in for the real apply. Records what it was handed, in order."""
    seen: list[str] = []

    def apply(obj):
        seen.append(obj.name)
        if fail and obj.name in fail:
            return False, "forbidden"
        return True, f"{obj.ref} created"

    return seen, apply


def _walk(states, **kwargs):
    seen, apply = _recorder(kwargs.pop("fail", None))
    kwargs.setdefault("interactive", True)
    kwargs.setdefault("assume_yes", False)
    return walk(states, "ns", "oc", apply=apply, **kwargs), seen


def test_nothing_pending_applies_nothing_and_asks_nothing() -> None:
    with patch("builtins.input", side_effect=AssertionError("must not ask")):
        result, applied = _walk([_state(CURRENT)])
    assert applied == []
    assert not result.changed_anything and not result.aborted


def test_an_object_already_correct_is_never_asked_about() -> None:
    """A prompt whose only sane answer is yes trains people to stop reading prompts."""
    with patch("builtins.input", return_value="y") as ask:
        result, applied = _walk([_state(CURRENT, "a"), _state(ABSENT, "b")])
    assert ask.call_count == 1
    assert applied == ["b"]


def test_each_yes_applies_immediately_rather_than_at_the_end() -> None:
    """Batching would mean a user who says yes twice and then stops is told nothing happened."""
    order: list[str] = []

    def apply(obj):
        order.append(f"apply:{obj.name}")
        return True, "created"

    def answer(*_args, **_kwargs):
        order.append("ask")
        return "y"

    with patch("builtins.input", side_effect=answer):
        walk([_state(ABSENT, "a"), _state(ABSENT, "b")], "ns", "oc",
             interactive=True, assume_yes=False, apply=apply)
    # Every apply sits between the question that caused it and the next question.
    assert order == ["ask", "apply:a", "ask", "apply:b"]


def test_skipping_one_still_applies_the_rest() -> None:
    with patch("builtins.input", side_effect=["y", "n", "y"]):
        result, applied = _walk([_state(ABSENT, "a"), _state(ABSENT, "b"), _state(ABSENT, "c")])
    assert applied == ["a", "c"]
    assert [o.name for o in result.skipped] == ["b"]


def test_all_applies_the_rest_without_asking_again() -> None:
    with patch("builtins.input", side_effect=["a"]) as ask:
        result, applied = _walk([_state(ABSENT, "a"), _state(ABSENT, "b"), _state(ABSENT, "c")])
    assert ask.call_count == 1
    assert applied == ["a", "b", "c"]


def test_quitting_after_a_yes_admits_what_was_already_applied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reporting "nothing was applied" after a yes is the lie this design exists to remove."""
    with patch("builtins.input", side_effect=["y", "q"]):
        result, applied = _walk([_state(ABSENT, "a"), _state(ABSENT, "b")])
    assert applied == ["a"]
    assert result.aborted and result.changed_anything
    printed = capsys.readouterr().out
    assert "1 object(s) were applied before you stopped" in printed
    assert "Nothing was applied" not in printed


def test_quitting_before_any_yes_says_nothing_was_applied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("builtins.input", side_effect=["q"]):
        result, applied = _walk([_state(ABSENT, "a"), _state(ABSENT, "b")])
    assert applied == []
    assert result.aborted
    assert "Nothing was applied" in capsys.readouterr().out


def test_a_failed_apply_is_reported_and_does_not_stop_the_walk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("builtins.input", return_value="y"):
        result, applied = _walk(
            [_state(ABSENT, "a"), _state(ABSENT, "b")], fail={"a"}
        )
    assert applied == ["a", "b"]                     # both were attempted
    assert [o.name for o, _ in result.failed] == ["a"]
    assert [o.name for o in result.applied] == ["b"]
    assert "could not be applied" in capsys.readouterr().out


def test_a_bare_enter_skips_rather_than_applies() -> None:
    """The default has to be the one that changes nothing."""
    with patch("builtins.input", return_value=""):
        _result, applied = _walk([_state(ABSENT)])
    assert applied == []


def test_an_unrecognized_answer_re_asks_and_never_counts_as_yes() -> None:
    with patch("builtins.input", side_effect=["maybe", "n"]) as ask:
        _result, applied = _walk([_state(ABSENT)])
    assert ask.call_count == 2
    assert applied == []


def test_a_closed_stdin_stops_rather_than_applying() -> None:
    with patch("builtins.input", side_effect=EOFError):
        result, applied = _walk([_state(ABSENT)])
    assert applied == [] and result.aborted


def test_escape_stops_the_walk() -> None:
    """The key people reach for to back out has to do that, not insert `^[` into a line."""
    from factory.contained import style

    with patch("factory.contained.style.read_key", return_value=style.ESCAPE):
        result, applied = _walk([_state(ABSENT), _state(ABSENT, "b")])
    assert applied == [] and result.aborted


def test_escape_typed_into_a_line_also_stops_the_walk() -> None:
    """Where a single keypress cannot be read, Escape is still recognized as line content."""
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", return_value="\x1b"):
        result, applied = _walk([_state(ABSENT)])
    assert applied == [] and result.aborted


def test_a_single_keypress_needs_no_enter() -> None:
    with patch("factory.contained.style.read_key", return_value="y"), \
         patch("builtins.input", side_effect=AssertionError("must not need Enter")):
        _result, applied = _walk([_state(ABSENT)])
    assert applied == ["factory"]


def test_an_arrow_key_is_ignored_rather_than_answered() -> None:
    """An escape *sequence* is navigation, not a decision, and must not read as Escape."""
    with patch("factory.contained.style.read_key", side_effect=["", "", "n"]) as key:
        _result, applied = _walk([_state(ABSENT)])
    assert key.call_count == 3
    assert applied == []


def test_the_prompt_spells_out_every_option() -> None:
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", return_value="n") as ask:
        _walk([_state(ABSENT)])
    question = ask.call_args[0][0]
    for spelled in ("[y]es", "[n]o", "[a]ll remaining", "[q]uit"):
        assert spelled in question


def test_yes_mode_applies_everything_pending_and_asks_nothing() -> None:
    states = [_state(CURRENT, "a"), _state(ABSENT, "b"), _state(DIFFERS, "c")]
    with patch("builtins.input", side_effect=AssertionError("must not ask")):
        _result, applied = _walk(states, assume_yes=True)
    assert applied == ["b", "c"]


def test_progress_is_visible_on_every_item(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("builtins.input", side_effect=["y", "y", "y"]):
        _walk([_state(ABSENT, "a"), _state(ABSENT, "b"), _state(ABSENT, "c")])
    printed = capsys.readouterr().out
    for position in ("1 of 3", "2 of 3", "3 of 3"):
        assert position in printed


def test_a_differing_object_shows_the_diff_not_the_manifest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Against an existing object the manifest is mostly lines that are already true."""
    with patch("builtins.input", return_value="n"):
        _walk([_state(DIFFERS, diff="-  verbs: [get]\n+  verbs: [get, list]\n")])
    printed = capsys.readouterr().out
    assert "verbs: [get, list]" in printed
    assert "kind: ServiceAccount" not in printed


def test_a_long_diff_is_trimmed_with_a_count(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("builtins.input", return_value="n"):
        _walk([_state(DIFFERS, diff="\n".join(f"+ line {n}" for n in range(200)))])
    printed = capsys.readouterr().out
    assert "more line(s)" in printed
    assert "+ line 199" not in printed


def test_an_uncomparable_object_says_so_before_showing_the_manifest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("builtins.input", return_value="n"):
        _walk([_state(UNKNOWN)])
    printed = capsys.readouterr().out
    assert "could not be compared" in printed
    assert "kind: ServiceAccount" in printed


def test_diff_is_asked_of_the_cluster_not_computed_locally() -> None:
    """A local comparison reads cluster-defaulted fields as changes the user is about to make."""
    with patch("factory.contained.k8s_review._run",
               side_effect=[_completed("serviceaccount/factory"), _completed("x", 1)]) as run:
        inspect_objects([_obj()], "ns", "oc")
    argv = run.call_args_list[1][0][0]
    assert argv[:2] == ["oc", "diff"]
    assert "-n" in argv and "ns" in argv
    assert run.call_args_list[1][1]["stdin"] == "kind: ServiceAccount\n"


def test_nothing_here_raises_on_a_broken_cli() -> None:
    with patch("factory.contained.k8s_review.subprocess.run", side_effect=FileNotFoundError):
        states = inspect_objects(bundle_objects(namespace="ns"), "ns", "oc")
    assert all(s.status == UNKNOWN for s in states)
    assert k8s_review is not None
