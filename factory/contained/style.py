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

Two things here are about *time* rather than appearance, and they answer complaints of the same
shape — "I could not tell whether it was working". `activity()` gives a slow step a line that says
what it is waiting for, rewritten in place; `read_secret()` gives a masked prompt the same
keystroke feedback an ordinary one has. Both degrade where the terminal cannot support them, and
`can_rewrite()` — not `enabled()` — is what decides that, because colour and motion are different
questions.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
import threading
import time
from types import TracebackType
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


# ------------------------------------------------------------------------------------------------
# Saying what a slow step is waiting for
# ------------------------------------------------------------------------------------------------

# Nothing is drawn before this. Every fast operation therefore produces byte-for-byte the output it
# produced before this existed, and a spinner that flashes for a third of a second — which reads as
# a glitch rather than as progress — is impossible by construction.
ACTIVITY_THRESHOLD_SECONDS = 5.0

# Fast enough that the elapsed counter looks live, slow enough to be free.
_ACTIVITY_FRAME_SECONDS = 0.1

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Return to column zero and clear to end of line. `\r` alone leaves the tail of a longer previous
# frame on screen, which is how a status line comes to read `waiting for the podd pod`.
_ERASE_LINE = "\r\033[2K"


def can_rewrite(stream: TextIO | None = None) -> bool:
    """Whether the last line on `stream` can be erased and redrawn in place.

    Deliberately *not* `enabled()`. Colour and motion are different questions with different
    answers: `FORCE_COLOR` in a CI job asks for colour in a log file, and honouring it as permission
    to emit carriage returns fills that log with thousands of half-lines. `NO_COLOR` likewise says
    nothing about redrawing. Only a real terminal gets rewritten; `FACTORY_NO_PROGRESS=1` opts out
    of even that.
    """
    target = stream if stream is not None else sys.stdout
    if os.environ.get("FACTORY_NO_PROGRESS"):
        return False
    if os.environ.get("TERM", "").strip().lower() == "dumb":
        return False
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def _elapsed(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class Activity:
    """A status line for one slow operation: what is running, and what it is waiting for.

    Use it through `activity()`. While one is live, nothing else may write to the same stream —
    another writer's output lands in the middle of a frame and is then erased by the next one.

    Off a terminal it degrades to plain lines rather than to silence, because the case that
    prompted this — a check that takes three minutes — is just as unreadable in a CI log as it is on
    a laptop. There it prints one line per *changed* description, so a log gains a progress trail
    instead of a thousand redraws.
    """

    def __init__(self, label: str, detail: str = "", *, stream: TextIO | None = None,
                 threshold: float = ACTIVITY_THRESHOLD_SECONDS) -> None:
        self._label = label
        self._detail = detail
        self._stream = stream if stream is not None else sys.stdout
        self._threshold = threshold
        self._rewrites = can_rewrite(self._stream)
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._frame = 0
        self._drawn = False
        self._announced: str | None = None

    def update(self, detail: str) -> None:
        """Say what is being waited for now. Safe to call as often as a poll loop likes."""
        with self._lock:
            if detail == self._detail:
                return
            self._detail = detail
            if self._rewrites:
                return
            # The plain path has no thread to notice the threshold passing, so the decision is made
            # here: quiet until the operation has proved slow, then one line per change.
            if time.monotonic() - self._started >= self._threshold and detail != self._announced:
                self._announced = detail
                self._write(f"   ... {self._compose_text()}\n")

    def _compose_text(self) -> str:
        return f"{self._label} — {self._detail}" if self._detail else self._label

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except (OSError, ValueError):
            # A closed stream must not take down the operation being reported on.
            self._rewrites = False

    def __enter__(self) -> Activity:
        self._started = time.monotonic()
        if self._rewrites:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            if self._drawn:
                # The caller's own result line goes where the spinner was. Erasing here rather than
                # leaving the frame up is what keeps a completed check looking like it always did.
                self._write(_ERASE_LINE)
                self._drawn = False

    def _spin(self) -> None:
        while not self._done.wait(_ACTIVITY_FRAME_SECONDS):
            if time.monotonic() - self._started < self._threshold:
                continue
            with self._lock:
                if self._done.is_set():
                    return
                self._draw()

    def _draw(self) -> None:
        spinner = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
        self._frame += 1
        clock = _elapsed(time.monotonic() - self._started)
        text = f"{spinner} {self._compose_text()} ({clock})"
        # Truncated to the terminal, because a frame that wraps occupies two lines and `\r` only
        # ever returns to the start of the last one — leaving the first half on screen forever.
        width = max(shutil.get_terminal_size(fallback=(80, 24)).columns - 1, 20)
        if len(text) > width:
            text = text[: width - 1] + "…"
        self._write(_ERASE_LINE + paint(text, "cyan", stream=self._stream))
        self._drawn = True


def activity(label: str, detail: str = "", *, stream: TextIO | None = None,
             threshold: float = ACTIVITY_THRESHOLD_SECONDS) -> Activity:
    """A status line for a slow operation, silent unless it turns out to be slow.

        with style.activity("inference_from_cluster", "creating the probe pod") as act:
            ...
            act.update("pod is Pending — ContainerCreating")

    It draws nothing for the first `threshold` seconds, redraws one line in place after that, and
    erases itself on the way out so the caller's result line lands in its place.
    """
    return Activity(label, detail, stream=stream, threshold=threshold)


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
    except ImportError:                       # pragma: no cover - non-POSIX only; termios always imports on the test platforms
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


def read_secret(
    question: str, *, mask: str = "*", stream: TextIO | None = None
) -> str | None:
    """Read a value that must not appear on screen. `None` means cancelled.

    Echoes one `mask` character per keystroke rather than nothing at all: a prompt that shows no
    response to typing is indistinguishable from one that is not receiving the keys, and the value
    being entered here is routinely a hundred-character paste. The length is the only thing the
    mask discloses.

    Falls back to `getpass`, which suppresses echo entirely, where raw reading is impossible. The
    returned value is stripped — a trailing newline or space from a copy-paste is never part of a
    key, and one that survives fails authentication in a way nothing reports usefully.

    The caller owns the value from here. It must not be logged, echoed, or put in an argv.
    """
    target = stream if stream is not None else sys.stdout
    rendered = f"{bold(question, stream=target)} "
    session = _raw_session(target)
    if session is None:
        import getpass

        try:
            typed = getpass.getpass(rendered)
        except (EOFError, OSError):
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
        return _edit_line(descriptor, target, mask=mask)
    except (OSError, ValueError):
        return None
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
        target.write("\n")
        target.flush()


def _edit_line(descriptor: int, target: TextIO, *, mask: str | None = None) -> str | None:
    """The line editor itself, on a terminal already in cbreak mode. `None` means cancelled.

    Small on purpose, and it echoes as it goes: cbreak turns off the line discipline that normally
    provides echo and Backspace, so anything it does not handle here is a key that appears to do
    nothing. The caller owns putting the terminal into cbreak and restoring it — this function only
    reads, and must not be called on a terminal that is still line-buffered.

    `mask` replaces each character on screen, for a value that must not be readable over a
    shoulder. The buffer is unaffected; only the echo changes.
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
            target.write(mask if mask is not None else char)
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


def select(
    question: str, options: list[tuple[str, str]], *, stream: TextIO | None = None
) -> str | None:
    """A one-keypress choice between named options. `None` means the user backed out.

    Each option is a `(key, label)` pair, printed one per line as `[k] label` — the same shape as
    `confirm`'s legend, for the same reason: a menu whose keys are only listed as `[a/b/c]` makes
    the reader hold the mapping in their head while deciding.

    Rejects an unknown key by asking again rather than by choosing something, because every caller
    of this is about to do something to a cluster.
    """
    target = stream if stream is not None else sys.stdout
    keys = {key.lower() for key, _ in options}
    print(file=target)
    for key, label in options:
        print(line(choice(key, f"  {label}", stream=target)), file=target)
    print(file=target)
    legend = f"{bold(question, stream=target)} [{'/'.join(key for key, _ in options)}]: "
    while True:
        pressed = read_key(legend, stream=target)
        if pressed is None:
            try:
                raw = input(legend)
            except (EOFError, OSError):
                print()
                return None
            if is_escape(raw):
                return None
            answer = raw.strip().lower()
            if answer in keys:
                return answer
            continue
        if pressed == ESCAPE:
            return None
        if pressed.lower() in keys:
            return pressed.lower()


def prompt(question: str, default: str | None = None, stream: TextIO | None = None) -> str:
    """A question, with its default rendered so it is obvious what Enter does."""
    if default is None:
        return f"{bold(question, stream=stream)} "
    return f"{bold(question, stream=stream)} [{paint(default, 'cyan', stream=stream)}] "
