"""Shared streaming helper for subprocess output."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import BinaryIO

import structlog

log = structlog.get_logger()


def should_stream() -> bool:
    """Determine if we should stream subprocess output to the terminal.

    Returns True unless:
    - FACTORY_RUNNER_QUIET=1 is set
    - stdout is not a TTY (e.g., piped to file)
    """
    if os.environ.get("FACTORY_RUNNER_QUIET", "").lower() in ("1", "true", "yes"):
        log.debug("should_stream", result=False, reason="FACTORY_RUNNER_QUIET")
        return False
    if not sys.stdout.isatty():
        log.debug("should_stream", result=False, reason="not_tty")
        return False
    log.debug("should_stream", result=True)
    return True


async def tee_stream(
    src: asyncio.StreamReader,
    dest: BinaryIO,
    buffer: list[bytes],
    *,
    stream: bool = True,
    prefix: bytes | None = None,
) -> None:
    """Read from an async stream, optionally tee to a destination, and collect in buffer.

    Args:
        src: Async stream reader (e.g., proc.stdout).
        dest: Destination file-like object (e.g., sys.stdout.buffer).
        buffer: List to collect all bytes read.
        stream: If True, write to dest as data arrives. If False, only buffer.
        prefix: Optional prefix to prepend to each line (e.g., b"[bob:researcher] ").
    """
    while True:
        line = await src.readline()
        if not line:
            break
        buffer.append(line)
        if stream:
            if prefix:
                dest.write(prefix)
            dest.write(line)
            dest.flush()


async def stream_subprocess(
    proc: asyncio.subprocess.Process,
    *,
    stream: bool = True,
    prefix: str | None = None,
) -> tuple[bytes, bytes]:
    """Stream subprocess stdout/stderr to the terminal while collecting output.

    Args:
        proc: The subprocess with PIPE for stdout and stderr.
        stream: If True, stream to sys.stdout/stderr. If False, only collect.
        prefix: Optional prefix for each line (e.g., "[bob:researcher]").

    Returns:
        (stdout_bytes, stderr_bytes) tuple with all collected output.
    """
    log.debug("stream_subprocess_start", stream=stream, prefix=prefix)

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
        ),
        tee_stream(
            proc.stderr,
            sys.stderr.buffer,
            stderr_buf,
            stream=stream,
            prefix=prefix_bytes,
        ),
    )

    await proc.wait()

    log.debug(
        "stream_subprocess_complete",
        returncode=getattr(proc, "returncode", None),
        stdout_bytes=sum(len(c) for c in stdout_buf),
        stderr_bytes=sum(len(c) for c in stderr_buf),
    )

    return b"".join(stdout_buf), b"".join(stderr_buf)
