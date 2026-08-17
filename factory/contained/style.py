"""Terminal styling for the parts of `contained` a person reads while deciding something.

Colour is used for **navigation**, not decoration: which step of a wizard you are on, whether a
check passed, and — the one that caused real confusion — which word in a sentence is a value you
chose rather than prose. "namespace default" reads as an adjective; `namespace 'default'` in cyan
reads as a name.

Everything degrades to plain text. `enabled()` is consulted at render time rather than at import,
because the same functions serve a terminal and a pipe in the same process, and a string built for
a TTY that then lands in a log file carries escape codes into it.

Precedence follows the conventions people already have configured:
`NO_COLOR` (any value, https://no-color.org) beats `FORCE_COLOR`, which beats TTY detection.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from typing import Any, TextIO

_RESET = "\033[0m"
_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}

# Wide enough for the longest fix line the checks emit, narrow enough to survive a split pane.
_MAX_WIDTH = 78


def enabled(stream: TextIO | None = None) -> bool:
    """Whether to emit escape codes to `stream` (stdout by default)."""
    target = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        # A closed or exotic stream is not a terminal, and asking must not raise inside output code.
        return False


def paint(text: str, *styles: str, stream: TextIO | None = None) -> str:
    """Wrap `text` in the named styles, or return it unchanged when colour is off."""
    if not styles or not enabled(stream):
        return text
    prefix = "".join(f"\033[{_CODES[s]}m" for s in styles if s in _CODES)
    return f"{prefix}{text}{_RESET}" if prefix else text


def bold(text: str, stream: TextIO | None = None) -> str:
    return paint(text, "bold", stream=stream)


def dim(text: str, stream: TextIO | None = None) -> str:
    return paint(text, "dim", stream=stream)


def value(text: str, stream: TextIO | None = None) -> str:
    """A value the user chose or the tool resolved — a namespace, a name, an image reference.

    Quoted as well as coloured. The quotes are what make it unambiguous where colour is unavailable,
    which is the case this exists for: "in namespace default" cannot be read without them.
    """
    return paint(f"'{text}'", "bold", "cyan", stream=stream)


def ok_mark(stream: TextIO | None = None) -> str:
    return paint("[ ok ]", "green", stream=stream)


def fail_mark(stream: TextIO | None = None) -> str:
    return paint("[FAIL]", "bold", "red", stream=stream)


def _width() -> int:
    return min(shutil.get_terminal_size(fallback=(80, 24)).columns, _MAX_WIDTH)


def section(title: str, *, step: int | None = None, total: int | None = None,
            stream: TextIO | None = None) -> str:
    """A wizard step header: a rule, the step's position, and what it is about.

    Steps are numbered because a setup that prints ten lines with no structure gives the reader no
    way to tell "still working" from "finished" — the complaint that prompted this.
    """
    label = f"{step}/{total}  {title}" if step is not None and total is not None else title
    if not enabled(stream):
        return f"\n-- {label} " + "-" * max(0, _width() - len(label) - 4)
    filled = _width() - len(label) - 4
    return (
        "\n"
        + paint(f"━━ {label} ", "bold", "cyan", stream=stream)
        + paint("━" * max(0, filled), "cyan", stream=stream)
    )


def subsection(title: str, *, step: int, total: int, stream: TextIO | None = None) -> str:
    """A header for one item *inside* a step, drawn lighter so the nesting is visible.

    A walk through four objects inside step 2 of 4 would otherwise print its own `1/4`…`4/4`
    directly under the wizard's, and the two numberings are unrelated. A single-weight rule and the
    spelled-out "1 of 4" keep them apart at a glance.
    """
    label = f"{step} of {total} · {title}"
    if not enabled(stream):
        return f"\n-- {label} " + "-" * max(0, _width() - len(label) - 4)
    return (
        "\n"
        + paint(f"── {label} ", "cyan", stream=stream)
        + paint("─" * max(0, _width() - len(label) - 4), "dim", stream=stream)
    )


def note(text: str, stream: TextIO | None = None) -> str:
    """Indented supporting detail under a step header, wrapped to the terminal.

    Dim, so it reads as secondary to the step title — which means it must not contain styled
    fragments of its own: a nested `value()` ends with a reset, and everything after it on the line
    silently stops being dim. Use `line()` for detail that has to highlight something.
    """
    return "\n".join(
        f"   {dim(chunk, stream=stream)}"
        for chunk in textwrap.wrap(text, width=_width() - 3) or [""]
    )


def field(label: str, rendered_value: str, *, pad: int = 10, stream: TextIO | None = None) -> str:
    """One aligned `Label:  value` row, for the facts a user checks before saying yes.

    The label is dim and the value is not, so a column of these reads as values with labels rather
    than as prose. `rendered_value` is passed through untouched — the caller has already decided
    whether it is a `value()`, a URL, or plain text.
    """
    return f"   {dim(f'{label}:'.ljust(pad), stream=stream)} {rendered_value}"


def line(text: str) -> str:
    """Indented detail that carries its own styling — a `value()`, a command, a path.

    Not wrapped: the things that go here are values and commands, and a wrapped command cannot be
    copied. Not dimmed either, for the reason in `note`.
    """
    return f"   {text}"


ESCAPE = "\x1b"
"""What `read_key` returns for a bare Escape, and what a text prompt looks for in a typed line."""

# How long to wait for the rest of an escape sequence before concluding the key was a bare Escape.
# Arrow keys and function keys arrive as ESC followed immediately by more bytes; a person pressing
# Escape produces one byte and nothing after it. 50ms is far longer than a local terminal needs to
# deliver the remainder and far shorter than anyone can press two keys.
_ESCAPE_SEQUENCE_WINDOW = 0.05


def is_escape(text: str) -> bool:
    """Whether a typed line was just Escape (possibly repeated), with nothing else on it.

    Line-buffered prompts cannot see Escape as a key — it arrives as a character in the line, which
    is why pressing it looks like `^[` and does nothing. A line that contains only escape
    characters was somebody trying to back out.
    """
    stripped = text.strip()
    return bool(stripped) and set(stripped) <= {ESCAPE, "[", "\x00"} and ESCAPE in stripped


def _raw_session(target: TextIO) -> tuple[int, Any] | None:
    """The file descriptor and saved terminal settings, or None when raw reading is impossible.

    Impossible means: not a terminal at either end, not POSIX, or a descriptor `termios` refuses.
    Every caller treats `None` as "fall back to `input()`" rather than as a failure.
    """
    try:
        if not (sys.stdin.isatty() and target.isatty()):
            return None
    except (AttributeError, ValueError):
        return None
    try:
        import termios
    except ImportError:                       # non-POSIX
        return None
    try:
        descriptor = sys.stdin.fileno()
        return descriptor, termios.tcgetattr(descriptor)
    except (termios.error, ValueError, OSError, AttributeError):
        return None


def _drain_escape_sequence(descriptor: int) -> bool:
    """True when bytes followed the Escape — i.e. it was a navigation key, not a cancel."""
    import select

    if not select.select([descriptor], [], [], _ESCAPE_SEQUENCE_WINDOW)[0]:
        return False
    while select.select([descriptor], [], [], 0)[0]:
        sys.stdin.read(1)
    return True


def read_key(question: str, stream: TextIO | None = None) -> str | None:
    """Read a single keypress, without waiting for Enter. `None` if the terminal cannot do it.

    Returns the character pressed, `ESCAPE` for a bare Escape, `"\\r"` for Enter, or `""` for a key
    that should be ignored (an arrow key, which arrives as an escape *sequence*). `None` means the
    caller should fall back to `input()` — not a terminal, not POSIX, or stdin is a pipe.

    Ctrl-C still interrupts: `cbreak` leaves signal generation on, clearing only line buffering and
    echo. The terminal is always restored, including when the caller is interrupted mid-read.
    """
    target = stream if stream is not None else sys.stdout
    session = _raw_session(target)
    if session is None:
        return None
    descriptor, original = session

    import termios
    import tty

    target.write(question)
    target.flush()
    try:
        tty.setcbreak(descriptor)
        char = sys.stdin.read(1)
        if char == ESCAPE:
            return "" if _drain_escape_sequence(descriptor) else ESCAPE
        return char
    except (OSError, ValueError):
        return None
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
        target.write("\n")
        target.flush()


# Keys the little line editor below has to handle itself, because cbreak turns off the line
# discipline that would otherwise do it.
_BACKSPACE = ("\x7f", "\x08")
_END_OF_TRANSMISSION = "\x04"


def read_line(
    question: str, default: str | None = None, stream: TextIO | None = None
) -> str | None:
    """Read a typed line where **Escape cancels the moment it is pressed**. `None` means cancelled.

    `input()` cannot do this. It is line-buffered, so Escape is delivered as a character in the
    line — which is why pressing it shows `^[` and nothing happens until Enter. Getting a cancel
    key to behave like one means reading characters as they arrive, which means echoing and
    handling Backspace here, since cbreak turns off the line discipline that normally does both.

    Falls back to `input()` where raw reading is impossible; there Escape is still recognised, but
    only once the line is submitted, because that is genuinely all a line-buffered prompt can see.
    """
    target = stream if stream is not None else sys.stdout
    rendered = prompt(question, default, stream=target)
    session = _raw_session(target)
    if session is None:
        try:
            typed = input(rendered)
        except (EOFError, OSError):
            # OSError, not just EOFError: a captured or closed stdin raises that instead. Both mean
            # nobody is there to answer, and both have to cancel rather than raise.
            print()
            return None
        return None if is_escape(typed) else typed.strip()

    descriptor, original = session

    import termios
    import tty

    target.write(rendered)
    target.flush()
    try:
        tty.setcbreak(descriptor)
        return _edit_line(descriptor, target)
    except (OSError, ValueError):
        return None
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
        target.write("\n")
        target.flush()


def _edit_line(descriptor: int, target: TextIO) -> str | None:
    """The line editor itself, on a terminal already in cbreak mode. `None` means cancelled.

    Small on purpose, and it echoes as it goes: cbreak turns off the line discipline that normally
    provides echo and Backspace, so anything it does not handle here is a key that appears to do
    nothing. The caller owns putting the terminal into cbreak and restoring it — this function only
    reads, and must not be called on a terminal that is still line-buffered.
    """
    typed_chars: list[str] = []
    while True:
        char = sys.stdin.read(1)
        if char == ESCAPE:
            if _drain_escape_sequence(descriptor):
                continue                      # an arrow key: not a cancel, and not text either
            return None
        if char in ("\r", "\n"):
            return "".join(typed_chars).strip()
        if char in _BACKSPACE:
            if typed_chars:
                typed_chars.pop()
                target.write("\b \b")         # move back, erase, move back again
                target.flush()
            continue
        if char in ("", _END_OF_TRANSMISSION):
            # Ctrl-D: end of input on an empty line, otherwise ignored as it would be in a shell.
            if not typed_chars:
                return None
            continue
        if char.isprintable():
            typed_chars.append(char)
            target.write(char)
            target.flush()


def choice(letter: str, rest: str, stream: TextIO | None = None) -> str:
    """One option in a multiple-choice prompt, as `[y]es` — the key to press, and what it does.

    A bare `[y/n/a/q]` is only readable to whoever wrote it. Spelling the word out while marking
    the letter costs one line and removes the guessing.
    """
    return paint(f"[{letter}]", "bold", "cyan", stream=stream) + rest


def confirm(question: str, *, default: bool = False, stream: TextIO | None = None) -> bool | None:
    """A yes/no question with the keys spelled out. `None` means the user backed out.

    Escape and end-of-input both return `None` rather than `False`, because "stop this" and "no,
    but carry on asking" are different answers and a caller that conflates them either loops
    forever or abandons work the user only meant to decline once.
    """
    legend = f"{choice('y', 'es', stream=stream)}  {choice('n', 'o', stream=stream)}"
    marker = "Y/n" if default else "y/N"
    text = f"{bold(question, stream=stream)}  {legend}  {dim(f'({marker})', stream=stream)}: "
    while True:
        key = read_key(text, stream=stream)
        if key is None:
            try:
                raw = input(text)
            except (EOFError, OSError):
                print()
                return None
            if is_escape(raw):
                return None
            answer = raw.strip().lower()
            if answer == "":
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            continue
        if key == ESCAPE:
            return None
        if key in ("\r", "\n"):
            return default
        if key.lower() == "y":
            return True
        if key.lower() == "n":
            return False


def prompt(question: str, default: str | None = None, stream: TextIO | None = None) -> str:
    """A question, with its default rendered so it is obvious what Enter does."""
    if default is None:
        return f"{bold(question, stream=stream)} "
    return f"{bold(question, stream=stream)} [{paint(default, 'cyan', stream=stream)}] "
