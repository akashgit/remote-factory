"""Shared plumbing for evidence probes.

Probes emit JSON records on stdout and diagnostics on stderr. They capture behavior; they never
decide whether a criterion passed. Anything resembling a verdict belongs in the judge, which does
not run in this process and cannot see this file.

Determinism matters more than convenience here: probes run under a fixed, fully-controlled
environment at fixed filesystem paths so that a composed command line is byte-comparable across
runs and across machines.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Fixed, not temporary. A random temp path would leak into composed command lines and make golden
# comparison (C21) impossible.
WORK_ROOT = Path("/tmp/factory-eval-contained")


_LOCK_PATH = Path("/tmp/factory-eval-contained.lock")
_LOCK_TIMEOUT_S = 600.0
_lock_handle: object | None = None


def _acquire_workspace_lock() -> None:
    """Serialise probe runs against each other.

    Probes work at fixed paths (see WORK_ROOT) because a random temp directory would leak into
    composed command lines and make golden comparison impossible. The cost is that two probe runs —
    a live collection and the meta-evaluation, say — will silently trample each other's fixtures and
    produce failures that look like real defects. This makes them queue instead.

    Held for the life of the process; released when it exits.
    """
    global _lock_handle
    if _lock_handle is not None:
        return
    import fcntl

    _LOCK_PATH.touch(exist_ok=True)
    handle = _LOCK_PATH.open("r+")
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_handle = handle
            return
        except OSError:
            if time.monotonic() > deadline:
                handle.close()
                raise TimeoutError(
                    f"another probe run has held {_LOCK_PATH} for over {_LOCK_TIMEOUT_S}s"
                ) from None
            time.sleep(0.25)


_acquire_workspace_lock()


def repo_root() -> Path:
    return Path(os.environ.get("FACTORY_EVAL_REPO_ROOT", ".")).resolve()


def factory_bin() -> list[str]:
    raw = os.environ.get("FACTORY_EVAL_FACTORY_BIN", "")
    if raw:
        parsed = json.loads(raw)
        if parsed:
            return [str(p) for p in parsed]
    found = shutil.which("factory")
    return [found] if found else []


def emit(record: dict[str, object]) -> None:
    """Write one evidence record to stdout."""
    sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
    sys.stdout.flush()


def note(message: str) -> None:
    """Diagnostics for a human reading the collector's stderr."""
    sys.stderr.write(f"{message}\n")


def fresh_dir(*parts: str) -> Path:
    """Create an empty directory at a fixed, deterministic path."""
    path = WORK_ROOT.joinpath(*parts)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def write_executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def extra_env() -> dict[str, str]:
    """Additional environment entries the collector asked every probe run to carry.

    Empty in an ordinary run, which is what keeps composed command lines reproducible. The
    meta-evaluation uses it to point probes at a mutated copy of the source tree without having to
    know how any individual probe builds its environment.
    """
    raw = os.environ.get("FACTORY_EVAL_EXTRA_ENV", "")
    if not raw:
        return {}
    parsed = json.loads(raw)
    return {str(k): str(v) for k, v in parsed.items()}


def run(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout: float = 120.0,
) -> dict[str, object]:
    """Run a command with an explicit environment and capture everything about it.

    Returns a dict suitable for embedding in an evidence record. The environment is passed
    wholesale — callers build it from scratch rather than inheriting, so what the command saw is
    exactly what the record reports.
    """
    env = {**env, **extra_env()}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd),
            env=env,
        )
        return {
            "command": cmd,
            "cwd": str(cwd),
            "env": dict(sorted(env.items())),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[:100_000],
            "stderr": proc.stderr[:100_000],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": cmd,
            "cwd": str(cwd),
            "env": dict(sorted(env.items())),
            "exit_code": None,
            "timed_out_after_s": timeout,
            "stdout": (exc.stdout or b"").decode("utf-8", "replace")[:100_000]
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")[:100_000],
            "stderr": (exc.stderr or b"").decode("utf-8", "replace")[:100_000]
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")[:100_000],
        }


def probe_record(
    criterion_id: str,
    tier: str,
    *,
    observations: dict[str, object],
    invocations: list[dict[str, object]],
) -> dict[str, object]:
    """Build a `status: ok` probe record.

    `invocations` holds the raw captured runs; `observations` holds mechanical restatements of
    what those runs contained (an argv list, a diff, a matched substring). The judge is instructed
    to check observations against the raw capture, so both travel together.
    """
    first = invocations[0] if invocations else {}
    return {
        "record": "probe",
        "id": criterion_id,
        "tier": tier,
        "status": "ok",
        "command": first.get("command"),
        "exit_code": first.get("exit_code"),
        "stdout": first.get("stdout", ""),
        "stderr": first.get("stderr", ""),
        "invocations": invocations,
        "observations": observations,
    }
