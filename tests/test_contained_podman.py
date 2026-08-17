"""Command composition for the local runtime.

This module only *composes* argv; execution lives in the CLI. That split is what makes
`FACTORY_CONTAINED_DRY_RUN=1` honest — dry-run prints the same list the real path executes rather
than a separate rendering that drifts. So these tests assert on exact argv, because an argv that is
merely "close" is the failure mode the split exists to prevent.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.podman import (
    CONTAINER_HOME,
    IDLE_COMMAND,
    LABEL_CONTAINED,
    TMUX_SESSION,
    ContainerPlan,
    Mount,
    build_create_argv,
    build_exec_argv,
    build_image_exists_argv,
    build_pane_liveness_argv,
    build_pull_argv,
    build_rm_argv,
    build_run_command,
    build_stat_argv,
    build_tmux_launch,
    container_name,
    dry_run_enabled,
    growth_context_warning,
    project_hash,
    resolve_image,
    scores_something,
)


def _plan(tmp_path: Path, **overrides: object) -> ContainerPlan:
    base: dict[str, object] = {
        "name": "rta-abc123",
        "image": "img:latest",
        "workdir": str(tmp_path / "rta"),
        "env": {"FACTORY_CONTAINED": "1"},
        "labels": {LABEL_CONTAINED: "true"},
        "mounts": (Mount(source=tmp_path / "rta", target=str(tmp_path / "rta")),),
        "run_command": "factory ceo /w/rta",
    }
    base.update(overrides)
    return ContainerPlan(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# Identity flags — the two that decide whether the workspace is writable
# --------------------------------------------------------------------------------------------


def test_a_userns_is_emitted_as_one_joined_flag(tmp_path: Path) -> None:
    """`--userns=keep-id` is one token; split into two, podman reads `keep-id` as the image."""
    argv = build_create_argv(_plan(tmp_path, userns="keep-id"))
    assert "--userns=keep-id" in argv
    assert "--user" not in argv


def test_an_explicit_user_is_emitted_as_a_flag_and_a_value(tmp_path: Path) -> None:
    argv = build_create_argv(_plan(tmp_path, user="501:0"))
    assert argv[argv.index("--user") + 1] == "501:0"


def test_the_container_is_created_with_an_init_around_an_idle_payload(tmp_path: Path) -> None:
    """The factory spawns agent subprocesses and is not a well-behaved init: without catatonit as
    PID 1 the container accumulates zombies and ignores `podman stop`."""
    argv = build_create_argv(_plan(tmp_path))
    assert argv[:4] == ["podman", "run", "-d", "--init"]
    assert argv[-3:] == ["sh", "-lc", IDLE_COMMAND]


def test_labels_and_env_are_ordered_so_two_runs_compose_identically(tmp_path: Path) -> None:
    """An argv that reorders between invocations makes a dry-run transcript uncomparable."""
    plan = _plan(tmp_path, env={"B": "2", "A": "1"}, labels={"z": "1", LABEL_CONTAINED: "true"})
    argv = build_create_argv(plan)
    assert argv.index("A=1") < argv.index("B=2")


# --------------------------------------------------------------------------------------------
# exec, and the flags that are stated rather than detected
# --------------------------------------------------------------------------------------------


def test_a_tty_is_requested_explicitly_rather_than_auto_detected() -> None:
    """The factory runs exec both from a terminal and from a pipe; auto-detection would quietly do
    the wrong thing in whichever case the caller forgot about."""
    assert build_exec_argv("c", ["sh"], tty=True) == ["podman", "exec", "-i", "-t", "c", "sh"]
    assert build_exec_argv("c", ["sh"]) == ["podman", "exec", "c", "sh"]


def test_a_detached_exec_carries_the_detach_flag_before_the_name() -> None:
    assert build_exec_argv("c", ["sh"], detach=True) == ["podman", "exec", "-d", "c", "sh"]


def test_liveness_asks_about_panes_rather_than_the_session() -> None:
    """The session is deliberately kept after the run ends so its output stays readable, so
    `has-session` reports a finished run as running."""
    argv = build_pane_liveness_argv("c")
    assert "#{pane_dead}" in argv
    assert "has-session" not in argv


def test_attaching_revives_a_dead_pane_first() -> None:
    """Attaching to a dead pane shows a frozen screen that accepts no input."""
    from factory.podman import build_attach_argv

    script = build_attach_argv("c")[-1]
    assert "respawn-pane" in script and "attach" in script


# --------------------------------------------------------------------------------------------
# The remaining single-purpose composers
# --------------------------------------------------------------------------------------------


def test_removal_forces_by_default_because_the_caller_already_decided() -> None:
    assert build_rm_argv("c") == ["podman", "rm", "--force", "c"]
    assert build_rm_argv("c", force=False) == ["podman", "rm", "c"]


def test_listing_can_be_narrowed_to_running_containers() -> None:
    """The label filter is not optional either way — a tool that shows a user resources it did not
    create invites them to assume it manages those too."""
    from factory.podman import build_ps_argv

    argv = build_ps_argv(all_states=False)
    assert "--all" not in argv
    assert f"label={LABEL_CONTAINED}=true" in argv


def test_image_helpers_check_existence_and_pull() -> None:
    assert build_image_exists_argv("i") == ["podman", "image", "exists", "i"]
    assert build_pull_argv("i") == ["podman", "pull", "i"]


def test_the_ownership_probe_can_be_pinned_to_a_user(tmp_path: Path) -> None:
    """Used to confirm a candidate identity actually sees the mount as its own."""
    mount = Mount(source=tmp_path, target="/w")
    argv = build_stat_argv("img", mount, user="501:0")
    assert argv[argv.index("--user") + 1] == "501:0"
    assert argv[-4:] == ["stat", "-c", "%u:%g", "/w"]


def test_a_read_only_mount_is_marked_ro() -> None:
    assert Mount(Path("/a"), "/b", read_only=True).as_flag() == "/a:/b:ro"
    assert Mount(Path("/a"), "/b").as_flag() == "/a:/b:rw"


# --------------------------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------------------------


def test_the_hash_is_never_what_gets_truncated() -> None:
    """Two same-named projects in different directories must not collide; the readable stem is
    what costs nothing but legibility when it is cut."""
    long_name = Path("/tmp/" + "a" * 60)
    name = container_name(long_name)
    assert len(name) <= 32
    assert name.endswith(project_hash(long_name)[:6])


def test_a_name_with_no_alphanumerics_still_produces_a_usable_container_name() -> None:
    name = container_name(Path("/tmp/---"))
    assert name.startswith("factory-")


# --------------------------------------------------------------------------------------------
# The container's shell line
# --------------------------------------------------------------------------------------------


def test_the_tmux_session_survives_the_factory_exiting() -> None:
    """A failed run is exactly when its state is worth reading."""
    launch = build_tmux_launch("/w", "factory ceo /w")
    assert "remain-on-exit on" in launch
    assert "pane-died detach-client" in launch
    assert shlex.quote(TMUX_SESSION) in launch or TMUX_SESSION in launch


def test_a_division_file_in_a_subdirectory_gets_its_directory_created(tmp_path: Path) -> None:
    """`printf > .factory/division/README.md` fails outright if the directory is not there."""
    command = build_run_command(
        "/w", "factory ceo /w", files={".factory/division/README.md": "brief"}
    )
    assert "mkdir -p .factory/division" in command
    assert command.index("mkdir -p") < command.index("> .factory/division/README.md")


def test_a_file_at_the_workspace_root_needs_no_mkdir() -> None:
    command = build_run_command("/w", "factory ceo /w", files={"NOTES.md": "x"})
    assert "mkdir -p" not in command


def test_an_mcp_registration_is_written_next_to_the_project() -> None:
    command = build_run_command("/w", "factory ceo /w", mcp_config={"mcpServers": {"podman": {}}})
    assert "> .mcp.json" in command


def test_the_payload_is_the_last_thing_the_container_runs() -> None:
    """Everything before it is preparation; a payload that ran first would race the seeding."""
    command = build_run_command("/w", "factory ceo /w")
    assert command.endswith("factory ceo /w")


# --------------------------------------------------------------------------------------------
# The score-comparability warning
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["ceo", "/p"], ["--flag", "run", "/p"], ["eval"]])
def test_payloads_that_can_produce_a_score_are_recognised(argv: list[str]) -> None:
    assert scores_something(argv)


@pytest.mark.parametrize("argv", [["backlog-list", "/p"], ["--flag"], []])
def test_payloads_that_cannot_produce_a_score_are_not(argv: list[str]) -> None:
    """Warning about score comparability ahead of `backlog-list` trains the user to skip warnings,
    which costs them the one that matters."""
    assert not scores_something(argv)


def test_the_warning_names_every_missing_variable() -> None:
    assert growth_context_warning({}, ["ceo", "/p"]) is not None
    warning = growth_context_warning({"FACTORY_MANAGED_DIRS": "/d"}, ["ceo", "/p"])
    assert warning is not None
    assert "FACTORY_VAULT_PATH" in warning and "FACTORY_MANAGED_DIRS" not in warning


def test_a_fully_configured_environment_warns_about_nothing() -> None:
    env = {"FACTORY_MANAGED_DIRS": "/d", "FACTORY_VAULT_PATH": "/v"}
    assert growth_context_warning(env, ["ceo", "/p"]) is None


def test_a_whitespace_only_value_counts_as_unset() -> None:
    env = {"FACTORY_MANAGED_DIRS": "   ", "FACTORY_VAULT_PATH": "/v"}
    warning = growth_context_warning(env, ["ceo", "/p"])
    assert warning is not None and "FACTORY_MANAGED_DIRS" in warning


# --------------------------------------------------------------------------------------------
# Environment-driven configuration
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "YES", " true "])
def test_dry_run_accepts_the_documented_truthy_spellings(value: str) -> None:
    assert dry_run_enabled({"FACTORY_CONTAINED_DRY_RUN": value})


@pytest.mark.parametrize("value", ["0", "", "no", "maybe"])
def test_anything_else_is_not_a_dry_run(value: str) -> None:
    """Reading an unrecognised value as truthy would silently provision nothing on a real run."""
    assert not dry_run_enabled({"FACTORY_CONTAINED_DRY_RUN": value})


def test_the_image_falls_back_to_the_published_default() -> None:
    from factory.podman import DEFAULT_IMAGE

    assert resolve_image({}) == DEFAULT_IMAGE
    assert resolve_image({"FACTORY_CONTAINED_IMAGE": "mine:dev"}) == "mine:dev"


def test_the_image_is_read_from_the_real_environment_when_none_is_given() -> None:
    with patch.dict(os.environ, {"FACTORY_CONTAINED_IMAGE": "mine:dev"}, clear=False):
        assert resolve_image() == "mine:dev"


def test_the_container_home_is_stated_rather_than_inherited() -> None:
    """The container runs under an arbitrary UID with no /etc/passwd entry, so an unstated $HOME
    becomes `/` and every dotfile is written to the image's read-only root."""
    assert CONTAINER_HOME.startswith("/") and CONTAINER_HOME != "/"
