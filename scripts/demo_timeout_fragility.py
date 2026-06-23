"""Live demonstrations of factory agent timeout fragility.

Uses the REAL factory subprocess/watchdog code paths to prove each failure mode.
Run: uv run python scripts/demo_timeout_fragility.py
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time


async def demo_1_no_newline_kills_active_process() -> None:
    """Active process writing without newlines is killed by watchdog."""
    print("=" * 60)
    print("DEMO 1: readline() kills active no-newline output")
    print("=" * 60)
    print()

    from factory.runners._stream import stream_subprocess

    script = (
        "import sys, time\n"
        "start = time.time()\n"
        "for i in range(50):\n"
        "    sys.stdout.write('.')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.1)\n"
        "elapsed = time.time() - start\n"
        "sys.stdout.write(f'\\nDone after {elapsed:.1f}s\\n')\n"
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    killed_by_watchdog: list[bool] = [False]
    t0 = time.monotonic()

    stdout_bytes, stderr_bytes = await stream_subprocess(
        proc, stream=False, inactivity_timeout=1.5, killed_by_watchdog=killed_by_watchdog,
    )

    elapsed = time.monotonic() - t0

    print(f"  Process PID:          {proc.pid}")
    print(f"  Elapsed time:         {elapsed:.2f}s")
    print(f"  Return code:          {proc.returncode} ({'SIGKILL' if proc.returncode == -9 else 'normal'})")
    print(f"  Killed by watchdog:   {killed_by_watchdog[0]}")
    print(f"  Stdout captured:      {stdout_bytes!r}")
    print(f"  Process was writing:  a dot every 0.1s (flushed, no newline)")
    print(f"  Inactivity timeout:   1.5s")
    print()
    print(f"  The process was ACTIVELY writing output every 100ms.")
    print(f"  But readline() never returned because there were no newlines.")
    print(f"  The watchdog saw 0 activity updates and killed it at {elapsed:.1f}s.")
    print()
    print(f"RESULT: Active process killed after {elapsed:.1f}s despite producing output every 100ms")
    print()


async def demo_2_chatty_stderr_masks_stuck_stdout() -> None:
    """stderr heartbeats fool the watchdog while stdout is completely stuck."""
    print("=" * 60)
    print("DEMO 2: Chatty stderr masks stuck stdout")
    print("=" * 60)
    print()

    from factory.runners._stream import stream_subprocess

    script = (
        "import sys, time\n"
        "start = time.time()\n"
        "for i in range(17):\n"
        "    sys.stderr.write(f'[heartbeat {i}] t={time.time()-start:.1f}s\\n')\n"
        "    sys.stderr.flush()\n"
        "    time.sleep(0.3)\n"
        "sys.stderr.write(f'[exit] total={time.time()-start:.1f}s\\n')\n"
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    killed_by_watchdog: list[bool] = [False]
    t0 = time.monotonic()

    stdout_bytes, stderr_bytes = await stream_subprocess(
        proc, stream=False, inactivity_timeout=1.0, killed_by_watchdog=killed_by_watchdog,
    )

    elapsed = time.monotonic() - t0
    stderr_lines = stderr_bytes.decode().strip().split("\n")

    print(f"  Process PID:          {proc.pid}")
    print(f"  Elapsed time:         {elapsed:.2f}s")
    print(f"  Return code:          {proc.returncode}")
    print(f"  Killed by watchdog:   {killed_by_watchdog[0]}")
    print(f"  Stdout bytes:         {len(stdout_bytes)} (ZERO — completely stuck)")
    print(f"  Stderr lines:         {len(stderr_lines)}")
    print(f"  Inactivity timeout:   1.0s")
    print()
    print(f"  stderr samples:")
    for line in stderr_lines[:3]:
        print(f"    {line}")
    print(f"    ... ({len(stderr_lines)} total heartbeats)")
    print()
    print(f"  stdout was SILENT for {elapsed:.1f}s (threshold: 1.0s).")
    print(f"  But stderr heartbeats every 0.3s reset the shared last_activity.")
    print(f"  The watchdog was completely fooled — it never fired.")
    print()
    print(f"RESULT: Stuck stdout ran for {elapsed:.1f}s undetected (threshold 1.0s) because stderr was chatty")
    print()


async def demo_3_max_timeout_preempts_inactivity() -> None:
    """max_timeout fires before inactivity_timeout, making it dead code."""
    print("=" * 60)
    print("DEMO 3: max_timeout preempts inactivity_timeout (dead config)")
    print("=" * 60)
    print()

    from factory.runners._subprocess import run_subprocess

    script = (
        "import sys, time\n"
        "start = time.time()\n"
        "i = 0\n"
        "while True:\n"
        "    sys.stdout.write(f'tick {i} at {time.time()-start:.1f}s\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.5)\n"
        "    i += 1\n"
    )

    t0 = time.monotonic()

    result = await run_subprocess(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        timeout=10.0,
        runner_name="demo",
        role="dead-config",
        max_timeout=3.0,
    )

    elapsed = time.monotonic() - t0

    print(f"  Elapsed time:         {elapsed:.2f}s")
    print(f"  Return code:          {result.return_code}")
    print(f"  Kill reason:          {result.stdout.strip()}")
    print(f"  inactivity_timeout:   10.0s (should allow the process to run)")
    print(f"  max_timeout:          3.0s (hard wall-clock cap)")
    print()
    print(f"  The process was outputting a line every 0.5s — well within the")
    print(f"  10.0s inactivity threshold. It should have run indefinitely.")
    print(f"  But max_timeout=3.0s killed it after {elapsed:.1f}s.")
    print()
    print(f"  IN PRODUCTION: The CEO is invoked with timeout=7200 (2h inactivity)")
    print(f"  but run_subprocess hardcodes max_timeout=3600 (1h wall clock).")
    print(f"  The 7200s inactivity timeout can NEVER fire — it's dead code.")
    print()
    print(f"RESULT: max_timeout={3.0} killed active process; inactivity_timeout={10.0} was dead code")
    print()


async def demo_4_proc_wait_hangs_forever() -> None:
    """proc.wait() blocks indefinitely after streams close."""
    print("=" * 60)
    print("DEMO 4: proc.wait() hangs forever after pipe redirect")
    print("=" * 60)
    print()

    from factory.runners._stream import stream_subprocess

    script = (
        "import os, sys, time\n"
        "sys.stdout.write('pre-redirect output\\n')\n"
        "sys.stdout.flush()\n"
        "devnull = os.open(os.devnull, os.O_WRONLY)\n"
        "os.dup2(devnull, 1)\n"
        "os.dup2(devnull, 2)\n"
        "os.close(devnull)\n"
        "time.sleep(120)\n"
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    killed_by_watchdog: list[bool] = [False]
    t0 = time.monotonic()

    hung = False
    try:
        await asyncio.wait_for(
            stream_subprocess(
                proc, stream=False, inactivity_timeout=30.0,
                killed_by_watchdog=killed_by_watchdog,
            ),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        hung = True
        t_hang = time.monotonic() - t0

    print(f"  Process PID:          {proc.pid}")
    print(f"  stream_subprocess hung: {hung}")
    if hung:
        print(f"  Hung for:             {t_hang:.2f}s before we gave up")
    print(f"  Process still alive:  {proc.returncode is None}")
    print(f"  Killed by watchdog:   {killed_by_watchdog[0]}")
    print()
    if hung:
        print(f"  The subprocess redirected its pipes to /dev/null, then kept running.")
        print(f"  tee_stream() saw EOF on both pipes and returned.")
        print(f"  The watchdog was cancelled in the finally block.")
        print(f"  Then proc.wait() was called — WITH NO TIMEOUT GUARD.")
        print(f"  It blocked for {t_hang:.1f}s until our external timeout caught it.")
        print(f"  Without that external guard, the factory would hang FOREVER.")
        print()

        ppid = None
        try:
            with open(f"/proc/{proc.pid}/status") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = line.split(":")[1].strip()
                        break
        except (FileNotFoundError, PermissionError):
            pass

        if ppid:
            print(f"  Process {proc.pid} parent PID: {ppid}")

    print()
    if hung:
        print(f"RESULT: stream_subprocess() hung for {t_hang:.1f}s at proc.wait() — no timeout guard")
    else:
        print("RESULT: (stream_subprocess returned normally — pipe redirect may not have worked)")

    # Cleanup
    if proc.returncode is None:
        proc.kill()
        await proc.wait()
    print()


async def demo_5_orphan_grandchild() -> None:
    """Grandchild processes survive parent kill — become orphans."""
    print("=" * 60)
    print("DEMO 5: Orphan grandchild processes on parent kill")
    print("=" * 60)
    print()

    from factory.runners._stream import stream_subprocess

    script = (
        "import subprocess, sys, time, os\n"
        "child = subprocess.Popen(['sleep', '120'])\n"
        "sys.stdout.write(f'CHILD_PID={child.pid}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(120)\n"
    )

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    killed_by_watchdog: list[bool] = [False]
    t0 = time.monotonic()

    stdout_bytes, _ = await stream_subprocess(
        proc, stream=False, inactivity_timeout=2.0, killed_by_watchdog=killed_by_watchdog,
    )

    elapsed = time.monotonic() - t0

    child_pid = None
    for line in stdout_bytes.decode().strip().split("\n"):
        if line.startswith("CHILD_PID="):
            child_pid = int(line.split("=")[1])

    print(f"  Parent PID:           {proc.pid}")
    print(f"  Parent return code:   {proc.returncode} ({'SIGKILL' if proc.returncode == -9 else 'normal'})")
    print(f"  Killed by watchdog:   {killed_by_watchdog[0]}")
    print(f"  Grandchild PID:       {child_pid}")

    if child_pid:
        # Check if grandchild is still alive
        try:
            os.kill(child_pid, 0)
            grandchild_alive = True
        except ProcessLookupError:
            grandchild_alive = False

        print(f"  Grandchild alive:     {grandchild_alive}")

        if grandchild_alive:
            # Read grandchild's parent PID from /proc
            ppid = None
            try:
                with open(f"/proc/{child_pid}/status") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            ppid = line.split(":")[1].strip()
                            break
            except (FileNotFoundError, PermissionError):
                pass

            print(f"  Grandchild parent:    PID {ppid} ({'orphan — reparented' if ppid != str(proc.pid) else 'still attached'})")
            print()
            print(f"  The watchdog killed the parent after {elapsed:.1f}s of inactivity.")
            print(f"  But the grandchild (sleep 120) is STILL RUNNING.")
            print(f"  It was reparented to PID {ppid} — an orphan process.")
            print(f"  In production, this would be a Builder's git/pytest subprocess")
            print(f"  continuing to run (and spend tokens) after the factory abandoned it.")
            print()
            print(f"  Contrast: factory/research/runner.py uses start_new_session=True")
            print(f"  + os.killpg() to kill the entire process group. The agent runner")
            print(f"  in _subprocess.py does NOT do this.")
            print()
            print(f"RESULT: Grandchild PID {child_pid} survived parent kill — orphaned to PID {ppid}")

            # Cleanup
            os.kill(child_pid, signal.SIGKILL)
        else:
            print()
            print("RESULT: (Grandchild died with parent — may vary by OS)")
    print()


async def demo_6_real_agent_timeout_race() -> None:
    """Demonstrate the real headless agent timeout race with factory agent."""
    print("=" * 60)
    print("DEMO 6: Real factory agent invocation — timeout race condition")
    print("=" * 60)
    print()

    from factory.runners._subprocess import run_subprocess

    # Check if claude is available
    import shutil
    claude_path = shutil.which("claude")
    if not claude_path:
        print("  SKIPPED: 'claude' CLI not found on PATH")
        print()
        return

    print(f"  Claude CLI found at: {claude_path}")
    print(f"  Invoking with inactivity_timeout=8s...")
    print(f"  Task: 'Say hello' (trivial, but headless mode buffers stdout)")
    print()

    t0 = time.monotonic()

    result = await run_subprocess(
        [
            claude_path,
            "-p", "Say hello and nothing else. One word.",
            "--output-format", "json",
        ],
        cwd=os.getcwd(),
        env=os.environ.copy(),
        timeout=8.0,
        runner_name="claude",
        role="demo",
        max_timeout=30.0,
    )

    elapsed = time.monotonic() - t0

    killed_by_inactivity = "killed after" in result.stdout.lower() or "inactivity" in result.stdout.lower()
    killed_by_max = "max wall-clock" in result.stdout.lower()

    print(f"  Elapsed time:         {elapsed:.2f}s")
    print(f"  Return code:          {result.return_code}")
    print(f"  Killed by inactivity: {killed_by_inactivity}")
    print(f"  Killed by max_timeout:{killed_by_max}")
    print(f"  Stdout length:        {len(result.stdout)} chars")

    if result.return_code == 0:
        # Agent completed before timeout — show it was a race
        print()
        print(f"  The agent completed in {elapsed:.1f}s (before the 8s threshold).")
        print(f"  This time it won the race. But with a longer task or slower API,")
        print(f"  the watchdog would have killed it. The outcome is non-deterministic")
        print(f"  because it depends on whether Claude CLI emits stderr during thinking.")
        print()
        stderr_text = result.metadata.get("stderr", "") if result.metadata else ""
        stderr_lines = [l for l in stderr_text.strip().split("\n") if l.strip()] if stderr_text.strip() else []
        print(f"  Stderr lines received: {len(stderr_lines)}")
        if stderr_lines:
            for line in stderr_lines[:5]:
                print(f"    {line[:100]}")
            if len(stderr_lines) > 5:
                print(f"    ... ({len(stderr_lines)} total)")
        print()
        print(f"RESULT: Agent survived with {elapsed:.1f}s — race condition (non-deterministic)")
    elif killed_by_inactivity:
        print()
        print(f"  The watchdog killed the agent after {elapsed:.1f}s of inactivity.")
        print(f"  Claude Code in headless mode produced no stderr output during")
        print(f"  its thinking phase, so the watchdog saw no activity.")
        print()
        print(f"RESULT: Real agent killed by inactivity watchdog after {elapsed:.1f}s")
    elif killed_by_max:
        print()
        print(f"RESULT: Real agent killed by max_timeout after {elapsed:.1f}s")
    else:
        print()
        print(f"  Agent output: {result.stdout[:200]}")
        print()
        print(f"RESULT: Agent failed with rc={result.return_code} after {elapsed:.1f}s")
    print()


async def main() -> None:
    print()
    print("FACTORY AGENT TIMEOUT FRAGILITY — LIVE DEMONSTRATIONS")
    print("Using real factory subprocess/watchdog code paths")
    print("=" * 60)
    print()

    await demo_1_no_newline_kills_active_process()
    await demo_2_chatty_stderr_masks_stuck_stdout()
    await demo_3_max_timeout_preempts_inactivity()
    await demo_4_proc_wait_hangs_forever()
    await demo_5_orphan_grandchild()
    await demo_6_real_agent_timeout_race()

    print("=" * 60)
    print("ALL DEMOS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
