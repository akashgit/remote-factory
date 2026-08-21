"""Walking the bundle object by object, against what the namespace already has.

Printing the whole bundle and asking "apply them?" asks the wrong question. Most of those objects
usually already exist, and the user cannot tell which — so the choice on offer is between "yes" and
"no" to a wall of YAML whose relationship to their cluster is unknown. What they actually need to
decide is, for each object that is *not* already right: what is this for, what would change, and do
I want it in my namespace.

So this establishes the current state first, then walks only the difference. Three states matter
and they are genuinely different decisions:

- **absent** — it would be created. The manifest is the whole story.
- **differs** — it exists and does not match. The *diff* is the story; the manifest is noise.
- **current** — nothing to decide. Reported once in the summary and never asked about, because a
  prompt whose only sane answer is "yes" trains people to stop reading prompts.

`oc diff` does the comparison server-side, which is the only way to get this right: it applies the
same merge the real apply would, so a field the cluster defaults in does not read as a change the
user is about to make.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from factory.contained import style
from factory.contained.bundle import BundleObject
from factory.contained.k8s import cli, is_auth_error, is_not_found

log = structlog.get_logger()

ABSENT = "absent"
DIFFERS = "differs"
CURRENT = "current"
UNKNOWN = "unknown"

# Long enough to show a real RBAC change, short enough that the question stays on screen with it.
_DIFF_LINES = 40


@dataclass(frozen=True)
class ObjectState:
    """One bundle object and how it compares to what the namespace already has."""

    obj: BundleObject
    status: str
    diff: str = ""
    detail: str = ""

    @property
    def needs_action(self) -> bool:
        return self.status != CURRENT


def _run(argv: list[str], *, stdin: str | None = None,
         timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv, input=stdin, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def inspect_objects(
    objects: list[BundleObject], namespace: str, binary: str,
    on_object: Callable[[BundleObject], None] | None = None,
) -> list[ObjectState]:
    """Compare each object against the cluster. Never raises; an unreadable object is `unknown`.

    `on_object` is called before each comparison, so a caller can say which one is being read. Two
    cluster round trips per object means a six-object bundle is comfortably long enough to look
    stopped.
    """
    states = []
    for obj in objects:
        if on_object is not None:
            on_object(obj)
        states.append(_inspect_one(obj, namespace, binary))
    return states


def _inspect_one(obj: BundleObject, namespace: str, binary: str) -> ObjectState:
    present = _run(cli(binary, "get", obj.kind, obj.name, "-n", namespace, "-o", "name"),
                   timeout=30)
    if present is None:
        return ObjectState(obj, UNKNOWN, detail=f"could not reach the cluster to check {obj.ref}")
    if present.returncode != 0:
        # **Only `NotFound` means absent.** Treating every non-zero exit as "not there" made an
        # expired login look like an empty namespace: all five objects were offered for creation
        # against a namespace that already had them, and the one honest line on screen — "could not
        # confirm whether the namespace exists" — was contradicted by the five under it.
        stderr = (present.stderr or "").strip()
        if is_not_found(stderr):
            return ObjectState(obj, ABSENT, detail="not in this namespace — it would be created")
        if is_auth_error(stderr):
            return ObjectState(
                obj, UNKNOWN,
                detail=f"could not be checked — not logged in to this cluster ({binary} login ...)",
            )
        first = stderr.splitlines()
        return ObjectState(
            obj, UNKNOWN,
            detail=(
                f"could not be checked: {first[0][:140]}" if first
                else f"could not be checked (exit {present.returncode})"
            ),
        )

    # `diff` exits 0 for no change and 1 for a change; anything higher is a real error, and so is 1
    # with nothing on stdout (some builds report a failure that way).
    diffed = _run(cli(binary, "diff", "-n", namespace, "-f", "-"),
                  stdin=obj.manifest, timeout=60)
    if diffed is None:
        return ObjectState(obj, UNKNOWN, detail=f"could not diff {obj.ref} against the cluster")
    if diffed.returncode == 0:
        return ObjectState(obj, CURRENT, detail="already present and matches what the factory needs")
    if diffed.returncode == 1 and diffed.stdout.strip():
        return ObjectState(obj, DIFFERS, diff=diffed.stdout,
                           detail="present, but not what the factory needs")
    reason = (diffed.stderr or "").strip().splitlines()
    return ObjectState(
        obj, UNKNOWN,
        detail=(
            f"present, but could not be compared ({reason[0][:120]})" if reason
            else "present, but could not be compared"
        ),
    )


_MARKS = {
    CURRENT: ("ok  ", "green"),
    ABSENT: ("new ", "cyan"),
    DIFFERS: ("diff", "yellow"),
    UNKNOWN: ("? ", "yellow"),
}


def _mark(status: str) -> str:
    text, colour = _MARKS.get(status, ("? ", "yellow"))
    return style.paint(f"[{text.strip():^4}]", colour)


def render_summary(states: list[ObjectState], namespace: str, server: str | None = None) -> str:
    """The whole picture in one block, before any question is asked.

    Deliberately covers *every* object including the ones already correct: "4 of 5 are already
    there" is the single most useful fact for someone deciding whether this tool is about to do
    something drastic to their namespace, and it is invisible if the correct ones are filtered out.

    The server belongs here rather than only on the last prompt, because with a per-object walk
    there is no single irreversible moment left to attach it to — the first `y` is already one.
    """
    width = max((len(s.obj.ref) for s in states), default=0)
    target = f"namespace {style.value(namespace)}"
    if server:
        target = f"{target} on {style.value(server)}"
    lines = [
        style.line(f"Comparing {len(states)} object(s) against {target}:"),
        "",
    ]
    lines += [f"   {_mark(s.status)} {s.obj.ref.ljust(width)}  {style.dim(s.detail)}"
              for s in states]
    pending = [s for s in states if s.needs_action]
    lines.append("")
    if not pending:
        lines.append(style.line(style.paint(
            "Everything the factory needs is already in place. Nothing to apply.", "green"
        )))
    else:
        already = len(states) - len(pending)
        settled = f"{already} already correct and will be skipped; " if already else ""
        lines.append(style.line(f"{settled}{style.bold(str(len(pending)))} need(s) your decision."))
    return "\n".join(lines)


def _trim(diff: str) -> str:
    lines = diff.splitlines()
    if len(lines) <= _DIFF_LINES:
        return diff.rstrip()
    remaining = len(lines) - _DIFF_LINES
    return "\n".join(lines[:_DIFF_LINES] + [f"... ({remaining} more line(s))"])


@dataclass
class WalkResult:
    """What the walk actually did — not what it intended to do.

    `applied` is the honest record and the reason this is not a plan: each object is applied the
    moment it is accepted, so stopping halfway leaves the cluster genuinely changed. Reporting
    "nothing was applied" after the user has already said yes twice is the failure this replaces.
    """

    applied: list[BundleObject] = field(default_factory=list)
    skipped: list[BundleObject] = field(default_factory=list)
    failed: list[tuple[BundleObject, str]] = field(default_factory=list)
    aborted: bool = False

    @property
    def changed_anything(self) -> bool:
        return bool(self.applied)


def walk(
    states: list[ObjectState],
    namespace: str,
    binary: str,
    *,
    interactive: bool,
    assume_yes: bool,
    apply: Callable[[BundleObject], tuple[bool, str]],
) -> WalkResult:
    """Walk each object that needs a decision, applying each one as it is accepted.

    Applying at the moment of consent rather than batching at the end is what makes the feedback
    immediate — you see `role/factory-runtime configured` before deciding the next one — and what
    makes stopping honest: whatever is already applied stays applied, and the summary says so.
    """
    result = WalkResult()
    pending = [s for s in states if s.needs_action]
    if not pending:
        return result

    total = len(pending)
    accept_rest = assume_yes or not interactive
    for index, state in enumerate(pending, start=1):
        if not accept_rest:
            print(_render_item(state, index, total, namespace))
            answer = _ask(index, total)
            if answer == "q":
                result.aborted = True
                break
            if answer == "n":
                result.skipped.append(state.obj)
                print(style.line(style.dim(f"Skipped {state.obj.ref}.")))
                continue
            if answer == "a":
                accept_rest = True
                print(style.line(f"Applying this and the {total - index} after it."))
        _apply_and_report(state.obj, apply, result)
    _report_totals(result)
    return result


def _apply_and_report(
    obj: BundleObject, apply: Callable[[BundleObject], tuple[bool, str]], result: WalkResult
) -> None:
    ok, detail = apply(obj)
    if ok:
        result.applied.append(obj)
        print(style.line(style.paint(detail or f"{obj.ref} applied.", "green")))
        return
    # A failure does not stop the walk. The objects are independent enough that the rest may still
    # be worth applying, and `verify` at the end reports exactly what is missing either way.
    result.failed.append((obj, detail))
    print(style.line(style.paint(f"{obj.ref} could not be applied: {detail}", "red")))


def _report_totals(result: WalkResult) -> None:
    if result.aborted:
        print()
        if result.changed_anything:
            print(style.line(style.paint(
                f"Stopped. {len(result.applied)} object(s) were applied before you stopped and "
                "remain applied; the rest were not.", "yellow"
            )))
        else:
            print(style.line(style.paint("Stopped. Nothing was applied.", "yellow")))
        return
    if result.skipped:
        print()
        print(style.line(
            f"{len(result.applied)} applied, {len(result.skipped)} skipped. A skipped object stays "
            "as it is, so `verify` will report it as missing or wrong."
        ))


def _render_item(state: ObjectState, index: int, total: int, namespace: str) -> str:
    kind = "would be created" if state.status == ABSENT else state.detail
    parts = [
        style.subsection(f"{state.obj.ref}  ({kind})", step=index, total=total),
        style.note(state.obj.purpose),
        "",
    ]
    if state.status == DIFFERS and state.diff.strip():
        # The diff, not the manifest: what is on screen should be what would change, and against an
        # existing object the manifest is mostly lines that are already true.
        parts.append(style.line(style.dim(f"What would change in {namespace}:")))
        parts.append(_trim(state.diff))
    elif state.status == UNKNOWN:
        parts.append(style.line(style.paint(
            "This could not be compared against the cluster, so what follows is what would be "
            "applied, not what would change.", "yellow"
        )))
        parts.append(state.obj.manifest.rstrip())
    else:
        parts.append(state.obj.manifest.rstrip())
    return "\n".join(parts)


# What each key does, spelled out. A bare `[y/n/a/q]` is readable only to whoever wrote it.
_OPTIONS = (
    ("y", "es", "y"),
    ("n", "o", "n"),
    ("a", "ll remaining", "a"),
    ("q", "uit", "q"),
)

# Typed answers accepted when falling back to a line-buffered prompt.
_WORDS = {
    "y": "y", "yes": "y",
    "n": "n", "no": "n", "": "n",
    "a": "a", "all": "a",
    "q": "q", "quit": "q", "exit": "q",
}


def _options_line() -> str:
    return "  ".join(style.choice(letter, rest) for letter, rest, _ in _OPTIONS)


def _ask(index: int, total: int) -> str:
    """One keypress per object. Anything unrecognized is treated as 'no', never as 'yes'.

    Escape quits, and quitting applies nothing. That needs the key itself rather than a typed line,
    so this reads raw where it can — which also means y/n/a/q take effect without Enter. Where it
    cannot (a pipe, a non-POSIX terminal) it falls back to a typed line, and there Escape is
    recognized as the *content* of the line, since that is all a line-buffered prompt ever sees.
    """
    question = (
        f"{style.bold(f'Apply this? ({index} of {total})')}  {_options_line()}  "
        f"{style.dim('(Enter or Esc = skip/stop)')}: "
    )
    while True:
        key = style.read_key(question)
        if key is None:
            answer = _ask_by_line(question)
            if answer is not None:
                return answer
            continue
        if key == style.ESCAPE:
            return "q"
        if key in ("\r", "\n"):
            return "n"
        if key == "":                       # an arrow key or similar — not an answer
            continue
        resolved = _WORDS.get(key.lower())
        if resolved is not None and key.strip():
            return resolved
        print(style.line(style.dim(_help_text())))


def _ask_by_line(question: str) -> str | None:
    """The fallback when a single keypress cannot be read. None means 'ask again'."""
    try:
        raw = input(question)
    except (EOFError, OSError):
        # The stream ended mid-walk, or there was never one. Refusing is the only safe reading.
        print()
        return "q"
    if style.is_escape(raw):
        return "q"
    resolved = _WORDS.get(raw.strip().lower())
    if resolved is not None:
        return resolved
    print(style.line(style.dim(_help_text())))
    return None


def _help_text() -> str:
    return (
        "y = apply this one, n = skip it, a = apply this and everything left, "
        "q or Esc = stop without applying anything"
    )
