"""Translating host paths in a passthrough command into their in-runtime equivalents.

The runtime does not share the host's filesystem layout, so a path in the passthrough command may
name something that does not exist inside. Teaching the host every subcommand's arguments is not an
option — a passthrough that second-guesses its payload breaks whenever the CLI grows — so one
generic rule applies instead: an argument that *resolves to an existing host path at or under the
project root* is translated; everything else is passed through untouched.

A path outside the project root is deliberately left alone. It will not exist in the runtime and the
command fails inside with a plain "no such file", which is the honest outcome: `--mount` is how such
a path is made available on purpose.

Locally the rewrite is usually a no-op, because the workspace copy is bind-mounted at its own
absolute path — identical inside and out (spec §2.5). That is not a reason to skip it: the payload
still names the *original* project path, which is a different directory from the copy, and the k8s
target rewrites to `/workspace/<name>` where nothing coincides.
"""

from __future__ import annotations

from pathlib import Path


def rewrite_argv(
    argv: list[str], project: Path, runtime_root: Path | str
) -> tuple[list[str], list[tuple[str, str]]]:
    """Rewrite in-project host paths to their runtime equivalents.

    Returns the new argv and the `(before, after)` pairs that changed, which the caller logs at
    launch so a surprising path in a later error message is traceable.
    """
    source = project.expanduser().resolve()
    target = Path(runtime_root)
    out: list[str] = []
    changes: list[tuple[str, str]] = []
    for token in argv:
        rewritten = _rewrite_one(token, source, target)
        if rewritten is None:
            out.append(token)
            continue
        out.append(rewritten)
        changes.append((token, rewritten))
    return out, changes


def _rewrite_one(token: str, source: Path, target: Path) -> str | None:
    """Return the translated token, or None when the token is not an in-project path."""
    if not token or token.startswith("-"):
        return None
    try:
        candidate = Path(token).expanduser().resolve()
    except (OSError, RuntimeError):
        # A token that is not a usable path at all — a prompt, a URL, a shell glob.
        return None
    if not candidate.exists():
        return None
    if candidate != source and source not in candidate.parents:
        return None
    relative = candidate.relative_to(source)
    result = target if relative == Path(".") else target / relative
    return None if str(result) == token else str(result)
