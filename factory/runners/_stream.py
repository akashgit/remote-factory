"""Shared streaming helper for subprocess output."""

from __future__ import annotations

import asyncio
import re
import sys
from typing import BinaryIO

# Robust multi-branch matcher for ANSI/VT escape sequences. Deliberately
# preserves \r and \n (they are not ANSI escapes). Covers five classes:
#   - CSI: \x1b[ <params 0x30-0x3F> <intermediates 0x20-0x2F> <final 0x40-0x7E>
#     (colors incl. colon-delimited truecolor, cursor moves, clear-screen,
#     alt-screen toggles \x1b[?1049h/l, cursor-visibility \x1b[?25l/h)
#   - OSC: \x1b] ... terminated by BEL (\x07) or ST (\x1b\\) — e.g. window title
#   - String-introducer DCS/SOS/PM/APC: \x1bP, \x1bX, \x1b^, \x1b_ carry a payload
#     terminated by BEL (\x07) or ST (\x1b\\). This branch MUST precede the Fe/C1
#     branch so the whole payload is consumed — otherwise Fe greedily matches just
#     the 2-byte introducer and the payload leaks as visible text.
#   - Fe / 2-byte C1: \x1b followed by 0x40-0x5F (incl. ESC M reverse line feed)
#   - Fp: \x1b7 (DECSC), \x1b8 (DECRC), \x1b= / \x1b> (keypad modes)
# The 8-bit C1 ST (\x9C) is intentionally NOT matched: on a raw byte stream that
# is later UTF-8 decoded, 0x9C is a valid continuation byte and matching it could
# clip a multibyte character. A lone trailing \x1b is left as-is.
# Known limitation: stripping is stateless and line-oriented (operates on one
# readline() chunk). An UNTERMINATED string/OSC sequence, or a sequence split
# across a readline() boundary, may leak its payload as visible text. This is
# low-probability for Bob (escape sequences normally arrive intact within one
# line) and fixing it would require stateful cross-line parsing — intentionally
# out of scope.
_ANSI_ESCAPE_RE = re.compile(
    rb"\x1B(?:"
    rb"\[[0-?]*[ -/]*[@-~]"              # CSI ... <final>
    rb"|\][^\x07\x1B]*(?:\x07|\x1B\\)"   # OSC ... (BEL or ST terminator)
    rb"|[PX^_][^\x07\x1B]*(?:\x07|\x1B\\)"  # DCS/SOS/PM/APC ... (BEL or ST terminator)
    rb"|[@-Z\\-_]"                        # 2-byte C1 / Fe (incl. ESC M)
    rb"|[78=>]"                           # Fp: DECSC, DECRC, keypad =/>
    rb")"
)


def strip_ansi(data: bytes) -> bytes:
    r"""Remove ANSI/VT escape sequences. Leaves \r, \n and plain text intact."""
    return _ANSI_ESCAPE_RE.sub(b"", data)


def should_stream() -> bool:
    """Determine if we should stream subprocess output to the terminal.

    Returns True unless:
    - FACTORY_RUNNER_QUIET=1 is set
    - stdout is not a TTY (e.g., piped to file)
    """
    from factory.user_config import resolve

    quiet = resolve("runner_quiet", env_var="FACTORY_RUNNER_QUIET") or ""
    if quiet.lower() in ("1", "true", "yes"):
        return False
    if not sys.stdout.isatty():
        return False
    return True


async def tee_stream(
    src: asyncio.StreamReader,
    dest: BinaryIO,
    buffer: list[bytes],
    *,
    stream: bool = True,
    prefix: bytes | None = None,
    sanitize: bool = False,
) -> None:
    """Read from an async stream, optionally tee to a destination, and collect in buffer.

    Args:
        src: Async stream reader (e.g., proc.stdout).
        dest: Destination file-like object (e.g., sys.stdout.buffer).
        buffer: List to collect all bytes read.
        stream: If True, write to dest as data arrives. If False, only buffer.
        prefix: Optional prefix to prepend to each line (e.g., b"[bob:researcher] ").
        sanitize: If True, strip ANSI/VT escape sequences from the bytes written to
            dest. The buffer always receives the raw line, never sanitized. Lines
            that contained ONLY escape sequences (empty after stripping, modulo
            \\r/\\n) are skipped entirely, including the prefix, so redraw-only TUI
            frames do not flood the terminal with bare prefixes. Genuine blank
            lines (no escapes) are preserved.
    """
    while True:
        line = await src.readline()
        if not line:
            break
        buffer.append(line)  # ALWAYS raw — the captured buffer is never sanitized
        if stream:
            out = strip_ansi(line) if sanitize else line
            if sanitize and out != line and not out.strip(b"\r\n"):
                continue  # drop redraw-only lines (avoids empty prefixed lines)
            if prefix:
                dest.write(prefix)
            dest.write(out)
            dest.flush()


async def stream_subprocess(
    proc: asyncio.subprocess.Process,
    *,
    stream: bool = True,
    prefix: str | None = None,
    sanitize: bool = False,
) -> tuple[bytes, bytes]:
    """Stream subprocess stdout/stderr to the terminal while collecting output.

    Args:
        proc: The subprocess with PIPE for stdout and stderr.
        stream: If True, stream to sys.stdout/stderr. If False, only collect.
        prefix: Optional prefix for each line (e.g., "[bob:researcher]").
        sanitize: If True, strip ANSI/VT escape sequences from the bytes written to
            the terminal (both stdout and stderr). The returned buffers stay raw.

    Returns:
        (stdout_bytes, stderr_bytes) tuple with all collected output.
    """
    stdout_buf: list[bytes] = []
    stderr_buf: list[bytes] = []

    prefix_bytes = f"{prefix} ".encode() if prefix else None

    assert proc.stdout is not None
    assert proc.stderr is not None

    await asyncio.gather(
        tee_stream(
            proc.stdout,
            sys.stdout.buffer,
            stdout_buf,
            stream=stream,
            prefix=prefix_bytes,
            sanitize=sanitize,
        ),
        tee_stream(
            proc.stderr,
            sys.stderr.buffer,
            stderr_buf,
            stream=stream,
            prefix=prefix_bytes,
            sanitize=sanitize,
        ),
    )

    await proc.wait()

    return b"".join(stdout_buf), b"".join(stderr_buf)
