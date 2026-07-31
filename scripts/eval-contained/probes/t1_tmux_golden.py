#!/usr/bin/env python3
# COVERS: C21
"""C21 — `factory tmux` must be byte-identical after the arg-builder extraction.

The composed shell command is captured by putting a fake `tmux` on PATH that records the argv it
was handed, then diffed against a golden recorded before the refactor. The whole run happens under
an explicit environment at fixed paths, because the composed command embeds `export PATH=...` and
every forwarded environment variable — anything ambient would make the golden unreproducible.

Record the golden (on the pre-refactor tree):

    python3 scripts/eval-contained/probes/t1_tmux_golden.py --record
"""

from __future__ import annotations

import difflib
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _probe_lib import (  # noqa: E402
    emit,
    factory_bin,
    fresh_dir,
    note,
    probe_record,
    run,
    write_executable,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

TMUX_SHIM = """#!/usr/bin/env bash
# Fake tmux. Records the composed shell command and answers the handful of queries cmd_tmux makes.
set -u
state="${SHIM_STATE:?SHIM_STATE must be set}"
case "${1:-}" in
  -V)
    echo "tmux 3.5a"
    ;;
  has-session)
    # cmd_tmux asks twice: once to check for an existing session (must be absent) and once after
    # launch to confirm the session is alive (must be present).
    n_file="$state/has_session_count"
    n=$(cat "$n_file" 2>/dev/null || echo 0)
    n=$((n + 1))
    echo "$n" > "$n_file"
    if [ "$n" -eq 1 ]; then exit 1; fi
    ;;
  new-session)
    for last in "$@"; do :; done
    printf '%s' "$last" > "$state/new_session_cmd"
    : > "$state/new_session_argv"
    for a in "$@"; do printf '%s\\n' "$a" >> "$state/new_session_argv"; done
    ;;
  capture-pane)
    ;;
  *)
    ;;
esac
exit 0
"""

# Every branch of the arg re-serializer that a single invocation can reach. Two cases are needed
# because --focus/--refine and --clean-pr/--no-clean-pr are mutually exclusive in practice.
CASES: dict[str, list[str]] = {
    "full": [
        "tmux",
        "PROJECT",
        "--mode",
        "improve",
        "--loop",
        "--interval",
        "900",
        "--max-cycles",
        "3",
        "--no-github",
        "--runner",
        "claude",
        "--profile",
        "probe-profile",
        "--focus",
        "probe focus",
        "--clean-pr",
        "--prompt",
        "/tmp/factory-eval-contained/spec.md",
        "--branch",
        "probe-branch",
        "--min-growth",
        "2",
        "--max-new",
        "5",
        "--discover-only",
        "--bg-agents",
        "--tmux-persist",
        "--use-profile",
    ],
    "refine": [
        "tmux",
        "PROJECT",
        "--mode",
        "auto",
        "--refine",
        "make the eval runner retry on transient failures",
        "--no-clean-pr",
        "--model",
        "explicit-model",
    ],
}

# Fixed so it lands in the golden verbatim. Includes variables that must be forwarded, and one
# that must not be.
FIXED_ENV = {
    "PATH": "/tmp/factory-eval-contained/shim:/usr/bin:/bin",
    "HOME": "/tmp/factory-eval-contained/home",
    "SHIM_STATE": "/tmp/factory-eval-contained/state",
    "LANG": "C",
    "FACTORY_MODEL": "probe-model",
    "FACTORY_MANAGED_DIRS": "/tmp/factory-eval-contained/managed",
    "FACTORY_VAULT_PATH": "/tmp/factory-eval-contained/vault",
    "ANTHROPIC_API_KEY": "probe-anthropic-key",
    "BOBSHELL_API_KEY": "probe-bob-key",
    "OPENAI_API_KEY": "probe-openai-key",
    "CODEX_API_KEY": "probe-codex-key",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLOUD_ML_REGION": "us-east5",
    "UNRELATED_PROBE_VAR": "must-not-be-forwarded",
}


def _setup() -> tuple[Path, Path, Path]:
    project = fresh_dir("project")
    shim_dir = fresh_dir("shim")
    state = fresh_dir("state")
    fresh_dir("home")
    write_executable(shim_dir / "tmux", TMUX_SHIM)
    (Path("/tmp/factory-eval-contained") / "spec.md").write_text("probe spec\n")
    return project, shim_dir, state


def _compose(case: str, argv: list[str], project: Path, state: Path) -> dict[str, object]:
    """Run `factory tmux` against the shim and return the capture plus the composed command."""
    for stale in ("has_session_count", "new_session_cmd", "new_session_argv"):
        (state / stale).unlink(missing_ok=True)
    cmd = factory_bin() + [a.replace("PROJECT", str(project)) for a in argv]
    capture = run(cmd, env=dict(FIXED_ENV), cwd=project, timeout=180.0)
    composed_path = state / "new_session_cmd"
    capture["composed_shell_command"] = (
        composed_path.read_text() if composed_path.exists() else None
    )
    argv_path = state / "new_session_argv"
    capture["tmux_argv"] = argv_path.read_text().splitlines() if argv_path.exists() else None
    capture["case"] = case
    return capture


def main() -> int:
    record_mode = "--record" in sys.argv
    if not factory_bin():
        note("t1_tmux_golden: no factory entry point; cannot compose anything")
        return 1

    project, _shim, state = _setup()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    invocations: list[dict[str, object]] = []
    diffs: dict[str, list[str]] = {}
    hashes: dict[str, dict[str, str | None]] = {}
    missing_golden: list[str] = []

    for case, argv in CASES.items():
        capture = _compose(case, argv, project, state)
        invocations.append(capture)
        composed = capture["composed_shell_command"]
        golden_path = GOLDEN_DIR / f"tmux_{case}.txt"

        if record_mode:
            if composed is None:
                note(f"t1_tmux_golden: case {case} composed nothing; refusing to record a golden")
                return 1
            golden_path.write_text(str(composed))
            note(f"t1_tmux_golden: recorded {golden_path}")
            continue

        golden = golden_path.read_text() if golden_path.exists() else None
        if golden is None:
            missing_golden.append(case)
        actual = composed if isinstance(composed, str) else None
        hashes[case] = {
            "golden_sha256": hashlib.sha256(golden.encode()).hexdigest() if golden else None,
            "actual_sha256": hashlib.sha256(actual.encode()).hexdigest() if actual else None,
        }
        diffs[case] = list(
            difflib.unified_diff(
                (golden or "").splitlines(),
                (actual or "").splitlines(),
                fromfile=f"golden/{golden_path.name}",
                tofile=f"actual/{case}",
                lineterm="",
            )
        )

    if record_mode:
        return 0

    byte_identical = (
        not missing_golden
        and all(not d for d in diffs.values())
        and all(h["golden_sha256"] == h["actual_sha256"] for h in hashes.values())
    )

    emit(
        probe_record(
            "C21",
            "t1",
            observations={
                "cases": sorted(CASES),
                "byte_identical": byte_identical,
                "missing_golden_for_cases": missing_golden,
                "hashes": hashes,
                "diffs": diffs,
                "fixed_env": dict(sorted(FIXED_ENV.items())),
                "golden_dir": str(GOLDEN_DIR),
            },
            invocations=invocations,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
