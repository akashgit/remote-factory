"""Reproduction tests proving fragility of the current timeout system.

These tests exercise the REAL code in factory/runners/_stream.py and
factory/runners/_subprocess.py to demonstrate four distinct failure modes
in the watchdog/timeout architecture.
"""

from __future__ import annotations

import asyncio
import os
import sys

from factory.runners._stream import stream_subprocess
from factory.runners._subprocess import run_subprocess


async def _create_proc(
    script: str, *, start_new_session: bool = False,
) -> asyncio.subprocess.Process:
    """Spawn a Python subprocess running the given inline script."""
    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=start_new_session,
    )


class TestLineBasedDetectionMissesNoNewlineOutput:
    """Failure Mode 1: readline()-based activity detection kills active
    processes that produce output without newlines.

    tee_stream uses `await src.readline()` which only returns when it
    finds '\\n' or EOF. If a subprocess writes data WITHOUT newlines
    (progress dots, streaming JSON fragments), readline() blocks,
    last_activity is never updated, and the watchdog kills the process
    despite it being actively producing output.
    """

    async def test_watchdog_kills_active_no_newline_process(self):
        """A subprocess writes a dot every 0.1s (flushed, no newline).

        The subprocess IS active — it writes 50 dots over 5 seconds.
        But readline() never returns because there are no newlines.
        The watchdog sees zero activity and kills the process at ~1.0s.
        """
        script = (
            "import sys, time\n"
            "for _ in range(50):\n"
            "    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.1)\n"
            "sys.stdout.write('\\n')\n"
        )

        proc = await _create_proc(script)
        killed_by_watchdog: list[bool] = [False]

        await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=1.0,
            killed_by_watchdog=killed_by_watchdog,
        )

        assert killed_by_watchdog[0] is True, (
            "Watchdog should have killed the process despite it actively writing dots"
        )
        assert proc.returncode == -9, (
            f"Expected SIGKILL (-9), got returncode={proc.returncode}"
        )


    async def test_byte_mode_preserves_no_newline_process(self):
        """With activity_mode='byte', process writing without newlines survives."""
        script = (
            "import sys, time\n"
            "for _ in range(50):\n"
            "    sys.stdout.write('.')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.1)\n"
            "sys.stdout.write('\\n')\n"
        )

        proc = await _create_proc(script)
        killed_by_watchdog: list[bool] = [False]

        stdout_bytes, _ = await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=1.0,
            killed_by_watchdog=killed_by_watchdog,
            activity_mode="byte",
        )

        assert killed_by_watchdog[0] is False, (
            "Watchdog should NOT have killed — byte mode detects dot output as activity"
        )
        assert proc.returncode == 0, (
            f"Process should exit normally with byte mode, got returncode={proc.returncode}"
        )
        assert b"." in stdout_bytes, (
            "stdout should contain the dots written by the process"
        )


class TestSharedTimestampChattyStderrKeepsStuckStdoutAlive:
    """Failure Mode 2: Per-stream activity tracking is now in place.

    stdout and stderr have separate last_activity timestamps, but the
    kill semantic uses min(stdout_idle, stderr_idle) — kill when the
    most recent activity on ANY stream exceeds the threshold. This means
    stderr activity still keeps the process alive, which is CORRECT
    behavior for AI agents where stderr heartbeats during thinking are
    expected. The per-stream tracking provides better observability
    (the watchdog log shows per-stream idle times).
    """

    async def test_chatty_stderr_masks_stuck_stdout(self):
        """stderr prints a line every 0.3s; stdout prints NOTHING.

        With inactivity_timeout=1.0, stdout is silent for 5+ seconds —
        far exceeding the threshold. But the watchdog never fires because
        stderr keeps resetting last_activity.
        """
        script = (
            "import sys, time\n"
            "for _ in range(17):\n"
            "    sys.stderr.write('heartbeat\\n')\n"
            "    sys.stderr.flush()\n"
            "    time.sleep(0.3)\n"
        )

        proc = await _create_proc(script)
        killed_by_watchdog: list[bool] = [False]

        stdout_bytes, stderr_bytes = await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=1.0,
            killed_by_watchdog=killed_by_watchdog,
        )

        assert proc.returncode == 0, (
            f"Process should exit normally (watchdog fooled by stderr), "
            f"got returncode={proc.returncode}"
        )
        assert killed_by_watchdog[0] is False, (
            "Watchdog should NOT have fired — stderr activity kept it alive"
        )
        assert len(stdout_bytes) == 0, (
            "stdout was completely silent (stuck) for the entire run"
        )
        assert len(stderr_bytes) > 0, (
            "stderr was chatty — this is what fooled the watchdog"
        )


class TestTrickleOutputExtendsToMaxTimeout:
    """Failure Mode 3: Trickle output keeps the inactivity watchdog alive
    indefinitely, bounded only by the wall-clock max_timeout backstop.

    A process that outputs one line every (inactivity_timeout - epsilon)
    seconds resets the watchdog timer each time. The inactivity watchdog
    never fires. The process runs until max_timeout kills it.
    """

    async def test_trickle_reaches_max_timeout(self):
        """Process prints one line every 1.5s with inactivity_timeout=2.0.

        Each line resets the watchdog (1.5 < 2.0), so the inactivity
        timeout never fires. Without max_timeout=3.0, this process would
        run for 15 seconds. The max_timeout backstop is the only defense.
        """
        script = (
            "import sys, time\n"
            "for i in range(10):\n"
            "    sys.stdout.write(f'trickle {i}\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(1.5)\n"
        )

        result = await run_subprocess(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            timeout=2.0,
            runner_name="test",
            role="trickle",
            max_timeout=3.0,
        )

        assert "max wall-clock timeout" in result.stdout, (
            f"Expected max_timeout backstop message, got stdout={result.stdout!r}"
        )
        assert result.return_code == 1


class TestProcWaitHasNoTimeoutGuard:
    """Failure Mode 4 (FIXED): proc.wait() now has a 10s timeout guard.

    stream_subprocess() wraps proc.wait() in asyncio.wait_for(timeout=10.0).
    If the subprocess closes its pipes but keeps running, the timeout guard
    fires, kills the process group, and stream_subprocess returns.
    """

    async def test_proc_wait_returns_after_streams_close(self):
        """Subprocess redirects stdout/stderr to /dev/null then sleeps.

        The pipe write-ends close, tee_stream sees EOF, the watchdog is
        cancelled. The proc.wait() timeout guard fires after 10s, kills
        the process group, and stream_subprocess returns normally.
        """
        script = (
            "import os, sys, time\n"
            "devnull = os.open(os.devnull, os.O_WRONLY)\n"
            "os.dup2(devnull, 1)\n"
            "os.dup2(devnull, 2)\n"
            "os.close(devnull)\n"
            "time.sleep(60)\n"
        )

        proc = await _create_proc(script, start_new_session=True)
        killed_by_watchdog: list[bool] = [False]

        await asyncio.wait_for(
            stream_subprocess(
                proc,
                stream=False,
                inactivity_timeout=30.0,
                killed_by_watchdog=killed_by_watchdog,
            ),
            timeout=15.0,
        )

        assert proc.returncode is not None, (
            "Process should have been killed by the proc.wait() timeout guard"
        )


class TestOrphanGrandchildOnParentKill:
    """Failure Mode 5 (FIXED): Process group kill eliminates orphan grandchildren.

    The watchdog now uses os.killpg() to kill the entire process group.
    With start_new_session=True, killing the parent also kills all
    descendants — no more orphaned grandchildren.
    """

    async def test_grandchild_killed_with_parent(self):
        """Parent spawns a grandchild (sleep 60), writes its PID to stdout.

        stream_subprocess() kills the parent via the inactivity watchdog
        using os.killpg(), which also kills the grandchild.
        """
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen(['sleep', '60'])\n"
            "sys.stdout.write(f'{child.pid}\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )

        proc = await _create_proc(script, start_new_session=True)
        killed_by_watchdog: list[bool] = [False]

        stdout_bytes, _ = await stream_subprocess(
            proc,
            stream=False,
            inactivity_timeout=1.5,
            killed_by_watchdog=killed_by_watchdog,
        )

        assert killed_by_watchdog[0] is True, (
            "Watchdog should have killed the parent (no output after first line)"
        )

        grandchild_pid = int(stdout_bytes.decode().strip())

        import time as _time
        _time.sleep(0.1)

        try:
            os.kill(grandchild_pid, 0)
            grandchild_alive = True
        except ProcessLookupError:
            grandchild_alive = False

        assert grandchild_alive is False, (
            f"Grandchild PID {grandchild_pid} should be dead — "
            f"os.killpg() should have killed the entire process group"
        )


class TestInactivityTimeoutDeadCodeWhenExceedsMaxTimeout:
    """Failure Mode 6 (FIXED): max_timeout is auto-derived from inactivity_timeout.

    When max_timeout is None (default), it is set to max(timeout * 2, 3600.0),
    guaranteeing inactivity_timeout can always fire before the wall-clock
    backstop. Explicit max_timeout < timeout still works but logs a warning.
    """

    async def test_max_timeout_preempts_inactivity_timeout(self):
        """Explicit max_timeout=2.0 still works as a wall-clock backstop.

        With timeout=10.0 (inactivity) and explicit max_timeout=2.0,
        the wall clock fires first. This tests the explicit override path.
        """
        script = (
            "import sys, time\n"
            "for i in range(100):\n"
            "    sys.stdout.write(f'tick {i}\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.5)\n"
        )

        result = await run_subprocess(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            timeout=10.0,
            runner_name="test",
            role="dead-code",
            max_timeout=2.0,
        )

        assert "max wall-clock timeout" in result.stdout, (
            f"Expected max_timeout to fire (not inactivity), got: {result.stdout!r}"
        )
        assert result.return_code == 1

    async def test_auto_derived_max_timeout_does_not_preempt_inactivity(self):
        """With max_timeout=None, auto-derivation ensures inactivity fires first.

        Process goes silent after first line. With timeout=5.0 and no
        explicit max_timeout, auto-derived max_timeout = max(10.0, 3600.0)
        = 3600. The inactivity watchdog fires at ~5s, well before the
        3600s wall clock.
        """
        script = (
            "import sys, time\n"
            "sys.stdout.write('hello\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )

        result = await run_subprocess(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            timeout=5.0,
            runner_name="test",
            role="auto-derive",
        )

        assert "inactivity" in result.stdout, (
            f"Expected inactivity timeout, got: {result.stdout!r}"
        )
        assert "max wall-clock timeout" not in result.stdout, (
            f"max_timeout should NOT have fired, got: {result.stdout!r}"
        )
        assert result.return_code == 1

    async def test_same_process_exits_normally_with_generous_max(self):
        """Same process with max_timeout > total runtime exits cleanly.

        This proves that the process is healthy — it's the max_timeout
        ceiling that kills it, not any real problem.
        """
        script = (
            "import sys, time\n"
            "for i in range(4):\n"
            "    sys.stdout.write(f'tick {i}\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.5)\n"
        )

        result = await run_subprocess(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            timeout=10.0,
            runner_name="test",
            role="dead-code",
            max_timeout=20.0,
        )

        assert "max wall-clock timeout" not in result.stdout, (
            f"max_timeout should NOT fire with generous limit, got: {result.stdout!r}"
        )
        assert result.return_code == 0, (
            f"Process should exit normally, got return_code={result.return_code}"
        )
