"""`ls`, `attach`, `rm`, `sync` — over runtimes the factory created, and only those (spec §2.3).

A tool that lists resources it did not create invites the user to assume it manages them too, so
every subcommand here filters on the factory's own label and refuses a name that does not carry it.
`resolve_runtime` returning `None` is the enforcement point: `attach`, `remove`, and `sync` all go
through it before touching anything.

`ls` is the one command that spans both targets — one table, local and cluster together — because a
user asking "what is running?" does not want to ask it twice. Everything else acts on a single
named runtime and takes its target from `--target`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from factory.contained.workspace import Workspace, contained_home, merge_hint
from factory.podman import (
    LABEL_CONTAINED,
    LABEL_PROJECT,
    LABEL_SOURCE,
    build_attach_argv,
    build_ps_argv,
    build_rm_argv,
)

log = structlog.get_logger()

# States in which nothing a delete could interrupt is still happening. Anything else — including a
# state we have never seen, or a blank one — is treated as active, which is the safe default for a
# check that guards a destructive operation.
_INACTIVE_STATES = frozenset({"exited", "stopped", "created", "dead", "removing", "succeeded",
                              "failed", "terminated", "error", "completed"})


@dataclass(frozen=True)
class Runtime:
    """One factory-created runtime, normalized across podman containers and cluster pods."""

    name: str
    target: str
    project: str
    state: str
    created: datetime | None = None
    source: str | None = None

    @property
    def active(self) -> bool:
        return self.state.strip().lower() not in _INACTIVE_STATES


class LifecycleError(RuntimeError):
    """Listing or acting on a runtime failed in a way the caller should report."""


def _podman_entries() -> list[dict[str, object]]:
    try:
        result = subprocess.run(build_ps_argv(), capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise LifecycleError(
            "`podman` is not installed or not on PATH. Install it and retry, or run "
            "`factory contained verify` for the full list of prerequisites."
        ) from exc
    if result.returncode != 0:
        raise LifecycleError(f"listing containers failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise LifecycleError(
            f"`podman ps` returned output that isn't JSON: {result.stdout.strip()[:200]!r}"
        ) from exc
    return payload if isinstance(payload, list) else []


def _labels_of(entry: dict[str, object]) -> dict[str, object]:
    raw = entry.get("Labels")
    return raw if isinstance(raw, dict) else {}


def _name_of(entry: dict[str, object]) -> str:
    names = entry.get("Names")
    if isinstance(names, list) and names:
        return str(names[0])
    return str(entry.get("Name", ""))


def _created_of(entry: dict[str, object]) -> datetime | None:
    """`podman ps --format json` reports Created as a unix timestamp in this podman line.

    Older builds emit an RFC-3339 string under the same key, so both are accepted and anything
    unparseable degrades to `None` (rendered as `?`) rather than raising inside a listing.
    """
    created = entry.get("Created")
    if isinstance(created, (int, float)):
        return datetime.fromtimestamp(created, tz=timezone.utc)
    if isinstance(created, str) and created.strip():
        try:
            return datetime.fromisoformat(created.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def local_runtimes() -> list[Runtime]:
    """Every container the factory created on this machine, running or not.

    `build_ps_argv` already selects on the factory's own label, so the label check below is a
    second, independent filter site rather than the only one.
    """
    runtimes = []
    for entry in _podman_entries():
        labels = _labels_of(entry)
        if str(labels.get(LABEL_CONTAINED, "")).lower() != "true":
            continue
        runtimes.append(
            Runtime(
                name=_name_of(entry),
                target="local",
                project=str(labels.get(LABEL_PROJECT, "")),
                state=str(entry.get("State", "unknown")),
                created=_created_of(entry),
                source=str(labels.get(LABEL_SOURCE, "")) or None,
            )
        )
    return runtimes


def list_runtimes(target: str | None = None) -> tuple[list[Runtime], list[str]]:
    """Runtimes for one target, or both when `target` is None.

    Returns the runtimes plus any notes about a target that could not be reached. A missing
    cluster context is a note, not a failure: `ls` on a laptop with no kubeconfig must still list
    the local containers.
    """
    runtimes: list[Runtime] = []
    notes: list[str] = []
    if target in (None, "local"):
        try:
            runtimes += local_runtimes()
        except LifecycleError as exc:
            if target == "local":
                raise
            notes.append(f"local: {exc}")
    if target in (None, "k8s"):
        try:
            from factory.contained.k8s import cluster_runtimes

            runtimes += cluster_runtimes()
        except ModuleNotFoundError:
            # The cluster half is a separate phase (spec §13). `ls` with no target is the one
            # command that spans both, and it must list what exists rather than fail on what does
            # not — a laptop with no cluster support installed still has local containers to show.
            if target == "k8s":
                raise
        except LifecycleError as exc:
            if target == "k8s":
                raise
            notes.append(f"k8s: {exc}")
    return runtimes, notes


def _format_age(created: datetime | None) -> str:
    if created is None:
        return "?"
    now = datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    seconds = int((now - created).total_seconds())
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render_table(runtimes: list[Runtime], notes: list[str] | None = None) -> str:
    if not runtimes:
        # "None" and "could not look" are different answers, and printing the first for the second
        # tells a user their fleet is empty when the engine is simply down.
        body = (
            "Nothing could be listed — see the note(s) below."
            if notes
            else "No contained runtimes. Start one with `factory contained -- ceo <path>`."
        )
    else:
        rows = [f"{'NAME':<34}{'TARGET':<8}{'PROJECT':<14}{'AGE':<6}{'STATE'}"]
        for runtime in runtimes:
            rows.append(
                f"{runtime.name:<34}"
                f"{runtime.target:<8}"
                f"{runtime.project:<14}"
                f"{_format_age(runtime.created):<6}"
                f"{runtime.state}"
            )
        body = "\n".join(rows)
    for note in notes or []:
        body += f"\n\nnote: {note}"
    return body


def resolve_runtime(name: str, runtimes: list[Runtime]) -> Runtime | None:
    """Find a factory-created runtime by name, or None when it is not one of ours."""
    return next((r for r in runtimes if r.name == name), None)


def _not_ours(name: str) -> int:
    print(
        f"contained: {name} is not a runtime `factory contained` created. "
        "`factory contained ls` shows the ones it manages.",
        file=sys.stderr,
    )
    return 1


def attach(name: str, target: str) -> int:
    """Attach to the run's tmux session, blocking until the user detaches or it ends.

    `subprocess.call` forks and waits rather than exec'ing — this process resumes when the tmux
    client exits — but for the user at the terminal, that client *is* their terminal in the
    meantime: `Ctrl-b d` detaches without stopping the run.
    """
    runtimes, _ = list_runtimes(target)
    runtime = resolve_runtime(name, runtimes)
    if runtime is None:
        return _not_ours(name)
    if not runtime.active:
        print(
            f"contained: {name} is {runtime.state}; there is no live session to attach to. "
            f"Its output is still readable with `podman logs {name}`.",
            file=sys.stderr,
        )
        return 1
    if runtime.target == "k8s":
        from factory.contained.k8s import build_pod_attach_argv

        return subprocess.call(build_pod_attach_argv(name))
    return subprocess.call(build_attach_argv(name))


def remove(name: str, target: str, *, assume_yes: bool, interactive: bool | None = None) -> int:
    """Delete a factory-created runtime.

    Prompts before deleting one that is still active (spec §2.3: "Prompts if the run is still
    active" — not a hard refusal). `--yes` skips the prompt for automation. When stdin is not a TTY
    and `--yes` was not passed, this refuses rather than hanging on an answer that will never come.
    """
    runtimes, _ = list_runtimes(target)
    runtime = resolve_runtime(name, runtimes)
    if runtime is None:
        return _not_ours(name)
    if runtime.active and not assume_yes:
        is_interactive = sys.stdin.isatty() if interactive is None else interactive
        if not is_interactive:
            print(
                f"contained: {name} is still active (state={runtime.state}). Re-run with --yes to "
                "delete it non-interactively.",
                file=sys.stderr,
            )
            return 1
        answer = input(f"{name} is still active (state={runtime.state}). Delete anyway? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print(f"contained: {name} was not deleted.", file=sys.stderr)
            return 1

    log.info("contained_remove_requested", name=name, state=runtime.state, target=runtime.target)
    if runtime.target == "k8s":
        from factory.contained.k8s import remove_cluster_runtime

        return remove_cluster_runtime(name, assume_yes=assume_yes)

    code = subprocess.call(build_rm_argv(name))
    if code != 0:
        log.warning("contained_remove_failed", name=name, exit_code=code)
        return code
    log.info("contained_remove_completed", name=name)
    # The division server is a *host* process the run depends on, so removing the run is what ends
    # it. Nothing else does: it is deliberately detached from the command that started it.
    from factory.contained.division import stop_recorded

    if stop_recorded(name):
        print(f"{name}: division endpoint stopped.")
    ws = workspace_for(name)
    if ws is not None:
        print(f"{name}: deleted. Workspace copy remains at {ws.path}.")
        print(merge_hint(ws))
    else:
        print(f"{name}: deleted.")
    return 0


def reap_stale(name: str) -> tuple[bool, str]:
    """Delete `name` if — and only if — it is a factory-created container no longer active.

    A failed run that leaves its container behind otherwise blocks every later invocation of the
    same name behind a bare "name already in use", with nothing pointing at how to get unstuck.
    Reaping automatically is safe exactly when the two checks `remove()` applies interactively both
    hold: the label confirms the factory created it, and the state confirms it is not doing
    something a delete could interrupt. A still-running container is deliberately left alone —
    a name collision can equally mean "you meant to reattach".

    Returns `(reaped, detail)`; `detail` explains the outcome either way, so a caller that could
    not reap automatically still has something concrete to put in front of the user.
    """
    try:
        runtime = resolve_runtime(name, local_runtimes())
    except LifecycleError as exc:
        return False, str(exc)
    if runtime is None:
        return False, f"{name} is not a runtime `factory contained` created"
    if runtime.active:
        return False, f"{name} is still active (state={runtime.state})"
    code = subprocess.call(build_rm_argv(name))
    if code != 0:
        return False, f"removing stale container {name} failed (exit {code})"
    log.info("contained_stale_reaped", name=name, state=runtime.state)
    return True, f"removed stale container {name} (was {runtime.state})"


def workspace_for(name: str) -> Workspace | None:
    """Reconstruct the `Workspace` `sync`/`rm` need for a named runtime.

    Nothing persists a run-name-to-source-path manifest, so this reconstructs it from the one place
    `materialize` leaves a record on disk: `contained_home()/<name>/` holds exactly one child
    directory — the workspace copy, named after the source project. For a git worktree, that copy's
    `.git` is a pointer file of the form `gitdir: <source>/.git/worktrees/<id>`, which is what lets
    the source path be recovered without ever having stored it.

    A plain rsync copy (non-git source) carries no such pointer, so for that case there is no way
    back to the source path from the copy alone, and this returns None. A worktree whose branch
    cannot be determined also returns None rather than a `Workspace` with an empty branch:
    `merge_hint` treats a worktree with a falsy branch as a plain copy and prints an rsync merge
    command for what is actually a git worktree, and wrong guidance is worse than "not found".
    """
    root = contained_home() / name
    if not root.is_dir():
        return None
    children = [child for child in root.iterdir() if child.is_dir()]
    if len(children) != 1:
        return None
    path = children[0]
    git_pointer = path / ".git"
    if not git_pointer.is_file():
        return None
    try:
        contents = git_pointer.read_text().strip()
    except OSError:
        return None
    if not contents.startswith("gitdir:"):
        return None
    worktree_git_dir = Path(contents.split(":", 1)[1].strip())
    if worktree_git_dir.parent.name != "worktrees":
        return None
    source = worktree_git_dir.parent.parent.parent
    branch_result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    if not branch:
        return None
    return Workspace(source=source, path=path, kind="worktree", branch=branch)


def sync(name: str, target: str) -> int:
    """Report how to get the workspace back. Nothing is ever merged automatically."""
    runtimes, _ = list_runtimes(target)
    runtime = resolve_runtime(name, runtimes)
    if runtime is None:
        return _not_ours(name)
    if runtime.target == "k8s":
        from factory.contained.k8s import sync_cluster_runtime

        return sync_cluster_runtime(name)
    ws = workspace_for(name)
    if ws is None:
        print(
            f"contained: no local workspace found for {name} under {contained_home()}. The copy "
            "may have been removed, or the project was not a git repository (no source path is "
            "recoverable from a plain copy).",
            file=sys.stderr,
        )
        return 1
    print(f"{name}: the workspace is already on this machine — a bind mount, not a transfer.")
    print(merge_hint(ws))
    return 0


def dispatch_lifecycle(args: argparse.Namespace) -> int:
    """Route a parsed `factory contained` lifecycle subcommand to its handler."""
    name = getattr(args, "name", None)
    target = getattr(args, "target", "local")
    try:
        if args.subcommand == "ls":
            # No target filter: one table, both targets (spec §2.3).
            runtimes, notes = list_runtimes(None)
            print(render_table(runtimes, notes))
            return 0
        if args.subcommand in ("attach", "rm", "sync"):
            if not isinstance(name, str):
                # `interpret()` already enforces this on the real CLI path; this is
                # belt-and-suspenders for any other caller that constructs args by hand.
                print(
                    f"contained: `factory contained {args.subcommand}` needs a runtime name.",
                    file=sys.stderr,
                )
                return 2
            if args.subcommand == "attach":
                return attach(name, target)
            if args.subcommand == "rm":
                return remove(
                    name,
                    target,
                    assume_yes=bool(getattr(args, "yes", False)),
                    interactive=sys.stdin.isatty(),
                )
            return sync(name, target)
    except LifecycleError as exc:
        print(f"contained: {exc}", file=sys.stderr)
        return 1
    print(
        f"contained: `{args.subcommand}` is not implemented yet by lifecycle dispatch.",
        file=sys.stderr,
    )
    return 2
