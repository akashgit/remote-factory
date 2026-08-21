"""The raw-terminal half of `style`, and the display branches the fallback tests never reach.

`tests/conftest.py` forces `style._raw_session` to return None so no test blocks on a keypress —
which is exactly why the raw paths (`read_key`, `read_line`, `read_secret`, `_edit_line`, and the
keypress arms of `confirm`/`select`) go uncovered. Here we go the other way on purpose: a real pty,
with `_raw_session` restored to the genuine implementation for the duration, so the raw code runs
against a real terminal.

The one trap worth naming: `sys.stdin` must read a byte straight from the fd (`os.read`), NOT through
a buffered text wrapper. `read_key`'s escape-sequence drain uses `select` on the fd, and a text
wrapper that has already pulled the `[` of an arrow key into its own buffer leaves `select` seeing
nothing on the fd — so an arrow reads as a bare Escape and the drain path never runs. `_RawStdin`
below reads one byte at a time from the kernel, which keeps `select` honest.
"""

from __future__ import annotations

import io
import os
import time
from unittest.mock import patch


from factory.contained import style

# Captured at import, BEFORE conftest's autouse fixture replaces the attribute with a None-returning
# stub. This is the genuine function, so the tests below can exercise it and the callers that use it.
_REAL_RAW_SESSION = style._raw_session


class _TtyBuf(io.StringIO):
    """A capture buffer that claims to be a terminal, for the `target`/echo side of a raw read."""

    def isatty(self) -> bool:
        return True


class _RawStdin:
    """A stdin backed directly by a pty slave fd — one unbuffered byte per `read`, so `select` works."""

    def __init__(self, fd: int, *, read_error: bool = False) -> None:
        self._fd = fd
        self._read_error = read_error

    def isatty(self) -> bool:
        return os.isatty(self._fd)

    def fileno(self) -> int:
        return self._fd

    def read(self, n: int = 1) -> str:
        if self._read_error:
            raise OSError("stdin read failed")
        return os.read(self._fd, n).decode()


class _pty:
    """A pty pair with `sys.stdin` pointed at the slave and the real `_raw_session` restored.

    Enter, get `(master_fd, target)`; write keystrokes with `feed()`; the raw `style` functions then
    behave as if a person were typing. Everything is torn down on exit.
    """

    def __init__(self, *, read_error: bool = False) -> None:
        self._read_error = read_error

    def __enter__(self) -> tuple[int, _TtyBuf]:
        import pty
        import termios
        import tty

        self._master, self._slave = pty.openpty()
        # Put the slave in cbreak up front. Bytes fed while the terminal is still in its default
        # canonical mode sit in a line buffer that `read(1)` cannot reach until a newline arrives —
        # which is a hang, since the code under test reads before we ever send one. cbreak makes each
        # byte immediately readable, which is the mode `read_key`/`_edit_line` run in anyway.
        tty.setcbreak(self._slave)
        self._target = _TtyBuf()

        # `tty.setcbreak` defaults to TCSAFLUSH, which throws away input already queued on the
        # terminal. The code under test calls it *after* we have fed our keystrokes, so with the
        # default those keystrokes vanish and the read blocks forever. TCSANOW switches mode without
        # discarding, which is exactly what a real interactive session does not need but a
        # feed-then-read test does.
        real_setcbreak = tty.setcbreak

        def _setcbreak_now(fd: int, when: int = termios.TCSANOW) -> None:
            real_setcbreak(fd, termios.TCSANOW)

        self._patches = [
            patch.object(tty, "setcbreak", _setcbreak_now),
            patch.object(style.sys, "stdin", _RawStdin(self._slave, read_error=self._read_error)),
            patch.object(style, "_raw_session", _REAL_RAW_SESSION),
        ]
        for p in self._patches:
            p.start()
        return self._master, self._target

    def feed(self, data: bytes) -> None:
        os.write(self._master, data)

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patches):
            p.stop()
        os.close(self._master)
        os.close(self._slave)


def _feed(master: int, data: bytes) -> None:
    os.write(master, data)


def _feed_staged(master: int, chunks: list[bytes], delay: float = 0.09) -> None:
    """Write chunks with a gap between them, from a background thread.

    Needed only where an escape sequence is followed by more input: `_drain_escape_sequence` drains
    *everything* currently buffered, so a single write of `arrow + text` loses the text. On a real
    keyboard the arrow's bytes arrive as one burst and the next keystroke comes later; the gap here
    (longer than the 50ms drain window) reproduces that so the drain stops at the arrow.
    """
    import threading

    def run() -> None:
        for chunk in chunks:
            time.sleep(delay)
            os.write(master, chunk)

    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------------------------
# Display helpers the fallback suite never calls
# ---------------------------------------------------------------------------------------------


def test_enabled_and_can_rewrite_swallow_a_stream_that_cannot_answer_isatty() -> None:
    """Asking a closed or exotic stream whether it is a terminal must not raise inside output code."""
    class _Broken(io.StringIO):
        def isatty(self) -> bool:
            raise ValueError("closed")

    with patch.dict("os.environ", {}, clear=True):
        assert style.enabled(_Broken()) is False
        assert style.can_rewrite(_Broken()) is False


def test_the_marks_and_headers_render_in_colour() -> None:
    tty = _TtyBuf()
    with patch.dict("os.environ", {"FORCE_COLOR": "1"}, clear=True):
        assert "\033" in style.ok_mark(stream=tty)
        assert "\033" in style.fail_mark(stream=tty)
        assert "━" in style.section("Step", step=1, total=3, stream=tty)
        assert "─" in style.subsection("Item", step=1, total=3, stream=tty)


def test_subsection_and_field_render_plain_too() -> None:
    plain = io.StringIO()
    assert "1 of 3" in style.subsection("Item", step=1, total=3, stream=plain)
    assert "Cluster:" in style.field("Cluster", "x", stream=plain)


# ---------------------------------------------------------------------------------------------
# Activity: the spinner thread and the draw internals
# ---------------------------------------------------------------------------------------------


def test_activity_write_disables_itself_when_the_stream_breaks() -> None:
    """A closed stream mid-run must not take down the operation being reported on."""
    class _Broken(_TtyBuf):
        def write(self, s: str) -> int:
            raise OSError("gone")

    act = style.Activity("x", stream=_Broken())
    act._write("anything")
    assert act._rewrites is False


def test_the_spinner_stays_silent_until_the_threshold_passes() -> None:
    """A wake before the threshold hits the `continue`, so nothing is drawn for a quick operation."""
    tty = _TtyBuf()
    with style.activity("x", "waiting", stream=tty, threshold=0.3):
        time.sleep(0.15)                       # one spinner wake (~0.1s) lands under the threshold
    assert tty.getvalue() == ""                # never crossed the threshold, so never drew


def test_the_spinner_notices_done_after_the_threshold_under_the_lock() -> None:
    """If the run finishes just as a frame is due, the drawn-check under the lock bails cleanly."""
    tty = _TtyBuf()
    act = style.Activity("x", stream=tty, threshold=0.0)
    act.__enter__()
    act._lock.acquire()                        # make the spinner block right where it would draw
    time.sleep(0.15)                           # let it wake, pass the threshold, and wait on the lock
    act._done.set()                            # now finish, so the post-lock check returns
    act._lock.release()
    act.__exit__(None, None, None)
    # Nothing was drawn (the draw was blocked then cancelled), so there is nothing to erase.
    assert "\r" not in tty.getvalue()


def test_a_long_frame_is_truncated_to_the_terminal_width() -> None:
    tty = _TtyBuf()
    act = style.Activity("x", "y" * 200, stream=tty, threshold=0.0)
    act._started = time.monotonic()
    with patch("factory.contained.style.shutil.get_terminal_size",
               return_value=os.terminal_size((40, 24))):
        act._draw()
    drawn = tty.getvalue()
    assert "…" in drawn                         # the overflow was cut, not wrapped


# ---------------------------------------------------------------------------------------------
# _raw_session itself
# ---------------------------------------------------------------------------------------------


def test_raw_session_returns_the_fd_and_saved_settings_on_a_real_tty() -> None:
    import pty

    master, slave = pty.openpty()
    try:
        with patch.object(style.sys, "stdin", _RawStdin(slave)):
            session = _REAL_RAW_SESSION(_TtyBuf())
        assert session is not None
        fd, saved = session
        assert fd == slave and isinstance(saved, list)
    finally:
        os.close(master)
        os.close(slave)


def test_raw_session_declines_when_the_target_is_not_a_terminal() -> None:
    import pty

    master, slave = pty.openpty()
    try:
        with patch.object(style.sys, "stdin", _RawStdin(slave)):
            assert _REAL_RAW_SESSION(io.StringIO()) is None      # target.isatty() is False
    finally:
        os.close(master)
        os.close(slave)


def test_raw_session_declines_when_isatty_raises() -> None:
    class _Broken:
        def isatty(self) -> bool:
            raise ValueError("closed")

    with patch.object(style.sys, "stdin", _Broken()):
        assert _REAL_RAW_SESSION(_TtyBuf()) is None


def test_raw_session_declines_when_termios_refuses_the_descriptor() -> None:
    import pty
    import termios

    master, slave = pty.openpty()
    try:
        with patch.object(style.sys, "stdin", _RawStdin(slave)), \
             patch("termios.tcgetattr", side_effect=termios.error("nope")):
            assert _REAL_RAW_SESSION(_TtyBuf()) is None
    finally:
        os.close(master)
        os.close(slave)


# ---------------------------------------------------------------------------------------------
# read_key on a real terminal
# ---------------------------------------------------------------------------------------------


def test_read_key_returns_a_single_character() -> None:
    with _pty() as (master, target):
        _feed(master, b"a")
        assert style.read_key("? ", stream=target) == "a"


def test_read_key_returns_escape_for_a_bare_escape() -> None:
    with _pty() as (master, target):
        _feed(master, b"\x1b")
        assert style.read_key("? ", stream=target) == style.ESCAPE


def test_read_key_ignores_an_arrow_key() -> None:
    """An arrow arrives as ESC + `[A`; the drain consumes the tail and the key is reported as ''."""
    with _pty() as (master, target):
        _feed(master, b"\x1b[A")
        assert style.read_key("? ", stream=target) == ""


def test_read_key_returns_none_when_the_read_fails() -> None:
    with _pty(read_error=True) as (master, target):
        assert style.read_key("? ", stream=target) is None


# ---------------------------------------------------------------------------------------------
# read_line / _edit_line on a real terminal
# ---------------------------------------------------------------------------------------------


def test_read_line_accepts_a_typed_line() -> None:
    with _pty() as (master, target):
        _feed(master, b"factory-yi\r")
        assert style.read_line("Namespace", stream=target) == "factory-yi"


def test_read_line_backspace_erases_a_character() -> None:
    with _pty() as (master, target):
        _feed(master, b"ab\x7fc\r")            # a, b, backspace, c -> "ac"
        assert style.read_line("?", stream=target) == "ac"


def test_read_line_escape_cancels_immediately() -> None:
    with _pty() as (master, target):
        _feed(master, b"\x1b")
        assert style.read_line("?", stream=target) is None


def test_read_line_an_arrow_key_is_neither_text_nor_cancel() -> None:
    with _pty() as (master, target):
        # Staged: the arrow first, then the text after the drain window, so the drain stops at the
        # arrow instead of swallowing the "x".
        _feed_staged(master, [b"\x1b[A", b"x\r"])
        assert style.read_line("?", stream=target) == "x"


def test_read_line_ctrl_d_on_an_empty_line_cancels() -> None:
    with _pty() as (master, target):
        _feed(master, b"\x04")
        assert style.read_line("?", stream=target) is None


def test_read_line_ctrl_d_mid_line_is_ignored() -> None:
    with _pty() as (master, target):
        _feed(master, b"a\x04b\r")             # the Ctrl-D between letters does nothing
        assert style.read_line("?", stream=target) == "ab"


# ---------------------------------------------------------------------------------------------
# read_secret on a real terminal
# ---------------------------------------------------------------------------------------------


def test_read_secret_masks_the_echo_but_returns_the_value() -> None:
    with _pty() as (master, target):
        _feed(master, b"s3kret\r")
        value = style.read_secret("API key", stream=target)
    assert value == "s3kret"
    echo = target.getvalue()
    assert "s3kret" not in echo                 # the value never appears on screen
    assert "*" * 6 in echo                      # one mask per character


# ---------------------------------------------------------------------------------------------
# confirm / select — keypress arms and the input() fallback arms
# ---------------------------------------------------------------------------------------------


def test_confirm_keypress_enter_takes_the_default_and_n_returns_false() -> None:
    with patch("factory.contained.style.read_key", return_value="\r"):
        assert style.confirm("?", default=True) is True
    with patch("factory.contained.style.read_key", return_value="n"):
        assert style.confirm("?") is False


def test_confirm_line_fallback_covers_yes_no_and_reask() -> None:
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=["y"]):
        assert style.confirm("?") is True
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=["no"]):
        assert style.confirm("?") is False
    # An unrecognised answer re-asks rather than guessing.
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=["huh?", "yes"]):
        assert style.confirm("?") is True


def test_select_keypress_reasks_on_an_unknown_key() -> None:
    with patch("factory.contained.style.read_key", side_effect=["z", "1"]):
        assert style.select("?", [("1", "local"), ("2", "k8s")]) == "1"


def test_select_line_fallback_covers_valid_reask_escape_and_eof() -> None:
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=["nope", "2"]):
        assert style.select("?", [("1", "local"), ("2", "k8s")]) == "2"
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", return_value="\x1b"):
        assert style.select("?", [("1", "local")]) is None
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=EOFError):
        assert style.select("?", [("1", "local")]) is None


# ---------------------------------------------------------------------------------------------
# The last few branch edges
# ---------------------------------------------------------------------------------------------


def test_activity_plain_update_is_silent_before_the_threshold() -> None:
    """Off a tty and still quick: `update` records the text but writes nothing yet."""
    plain = io.StringIO()
    act = style.Activity("x", stream=plain, threshold=5.0)
    act._started = time.monotonic()
    act.update("waiting")
    assert plain.getvalue() == ""


def test_read_line_returns_none_when_the_raw_read_fails() -> None:
    with _pty(read_error=True) as (master, target):
        assert style.read_line("?", stream=target) is None


def test_read_secret_returns_none_when_the_raw_read_fails() -> None:
    with _pty(read_error=True) as (master, target):
        assert style.read_secret("?", stream=target) is None


def test_edit_line_ignores_backspace_on_an_empty_line() -> None:
    with _pty() as (master, target):
        _feed(master, b"\x7fab\r")             # backspace with nothing to erase, then "ab"
        assert style.read_line("?", stream=target) == "ab"


def test_edit_line_ignores_an_unhandled_control_character() -> None:
    with _pty() as (master, target):
        _feed(master, b"\x01a\r")              # Ctrl-A: not a key it handles, and not printable
        assert style.read_line("?", stream=target) == "a"


def test_confirm_keypress_reasks_on_an_unrelated_key() -> None:
    with patch("factory.contained.style.read_key", side_effect=["q", "y"]):
        assert style.confirm("?") is True
