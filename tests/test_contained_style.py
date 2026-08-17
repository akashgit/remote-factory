"""Terminal styling — that it navigates when there is a terminal, and vanishes when there is not.

The second half is the one worth testing: every one of these strings also lands in a pipe, a log
file and a CI transcript, and an escape code there is corruption rather than colour.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from factory.contained import style

ESC = "\033"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_nothing_is_emitted_to_a_pipe() -> None:
    plain = io.StringIO()
    assert ESC not in style.paint("hello", "bold", "red", stream=plain)
    assert style.value("ns", stream=plain) == "'ns'"
    assert ESC not in style.section("Step", step=1, total=3, stream=plain)


def test_a_terminal_gets_colour() -> None:
    tty = _Tty()
    with patch.dict("os.environ", {}, clear=True):
        assert ESC in style.paint("hello", "bold", stream=tty)


def test_no_color_beats_force_color() -> None:
    """https://no-color.org — an explicit opt-out wins over an explicit opt-in."""
    tty = _Tty()
    with patch.dict("os.environ", {"NO_COLOR": "1", "FORCE_COLOR": "1"}, clear=True):
        assert ESC not in style.paint("hello", "bold", stream=tty)


def test_force_color_beats_a_pipe() -> None:
    with patch.dict("os.environ", {"FORCE_COLOR": "1"}, clear=True):
        assert ESC in style.paint("hello", "bold", stream=io.StringIO())


def test_a_dumb_terminal_gets_no_escape_codes() -> None:
    tty = _Tty()
    with patch.dict("os.environ", {"TERM": "dumb"}, clear=True):
        assert ESC not in style.paint("hello", "bold", stream=tty)


def test_a_value_is_quoted_even_without_colour() -> None:
    """The complaint this exists for: "in namespace default" cannot be parsed by eye.

    Colour alone does not fix it, because the same sentence is read in pipes and logs.
    """
    plain = io.StringIO()
    assert "'default'" in f"namespace {style.value('default', stream=plain)}"


def test_a_section_states_its_position() -> None:
    plain = io.StringIO()
    rendered = style.section("Namespace", step=1, total=4, stream=plain)
    assert "1/4" in rendered and "Namespace" in rendered


def test_a_note_wraps_and_stays_indented() -> None:
    plain = io.StringIO()
    rendered = style.note("word " * 60, stream=plain)
    assert len(rendered.splitlines()) > 1
    assert all(chunk.startswith("   ") for chunk in rendered.splitlines())


def test_a_prompt_shows_what_enter_does() -> None:
    plain = io.StringIO()
    assert "[default]" in style.prompt("Namespace", "default", stream=plain)


# ---------------------------------------------------------------------------------------------
# Choices, and backing out
# ---------------------------------------------------------------------------------------------


def test_a_choice_spells_the_word_out_and_marks_the_key() -> None:
    """`[y/n/a/q]` is readable only to whoever wrote it."""
    plain = io.StringIO()
    assert style.choice("a", "ll remaining", stream=plain) == "[a]ll remaining"


def test_escape_is_recognized_in_a_typed_line() -> None:
    """A line-buffered prompt never sees Escape as a key — it arrives as content."""
    assert style.is_escape("\x1b")
    assert style.is_escape("\x1b\x1b")
    assert style.is_escape("  \x1b  ")


def test_ordinary_input_is_not_mistaken_for_escape() -> None:
    for text in ("", "y", "factory-yi", "n", "  "):
        assert not style.is_escape(text)


def test_read_key_declines_when_stdin_is_not_a_terminal() -> None:
    """Returning None is the signal to fall back to `input()`, not an error."""
    assert style.read_key("? ", stream=io.StringIO()) is None


def test_confirm_falls_back_to_a_line_and_takes_its_default() -> None:
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", return_value=""):
        assert style.confirm("Create it?", default=False) is False
        assert style.confirm("Create it?", default=True) is True


def test_confirm_returns_none_on_escape_which_is_not_no() -> None:
    """"Stop this" and "no, keep asking" are different answers."""
    with patch("factory.contained.style.read_key", return_value=style.ESCAPE):
        assert style.confirm("Create it?") is None
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", return_value="\x1b"):
        assert style.confirm("Create it?") is None


def test_confirm_returns_none_at_end_of_input() -> None:
    with patch("factory.contained.style.read_key", return_value=None), \
         patch("builtins.input", side_effect=EOFError):
        assert style.confirm("Create it?") is None


def test_read_line_cancels_on_escape_without_waiting_for_enter() -> None:
    """The whole point: `input()` cannot see Escape, so a cancel key needs raw reading."""
    with patch("factory.contained.style._raw_session", return_value=None), \
         patch("builtins.input", return_value="\x1b"):
        assert style.read_line("Namespace", "default") is None


def test_read_line_returns_the_typed_value_stripped() -> None:
    with patch("factory.contained.style._raw_session", return_value=None), \
         patch("builtins.input", return_value="  factory-yi  "):
        assert style.read_line("Namespace", "default") == "factory-yi"


def test_read_line_returns_empty_for_a_bare_enter_so_the_default_applies() -> None:
    """Empty is not cancelled: the caller substitutes its default, which `None` would skip."""
    with patch("factory.contained.style._raw_session", return_value=None), \
         patch("builtins.input", return_value=""):
        assert style.read_line("Namespace", "default") == ""


def test_read_line_cancels_when_stdin_is_captured_or_closed() -> None:
    """pytest's stdin raises OSError rather than EOFError; both mean nobody is there."""
    for failure in (EOFError, OSError):
        with patch("factory.contained.style._raw_session", return_value=None), \
             patch("builtins.input", side_effect=failure):
            assert style.read_line("Namespace") is None


def test_confirm_reads_a_single_keypress() -> None:
    with patch("factory.contained.style.read_key", return_value="y"), \
         patch("builtins.input", side_effect=AssertionError("must not need Enter")):
        assert style.confirm("Create it?") is True
