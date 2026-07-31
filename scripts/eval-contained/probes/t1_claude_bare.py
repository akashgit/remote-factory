#!/usr/bin/env python3
# COVERS: C3,C4
"""C3/C4 — `--bare` must be passed to Claude Code in sandbox mode and only in sandbox mode.

Without `--bare`, Claude Code attempts its OAuth login flow inside a sandbox, where nothing can
complete it. Adding it unconditionally would change every ordinary invocation on a developer's
machine, so the pair of criteria is deliberately opposed: C3 wants it present, C4 wants it absent,
and only an implementation that scopes the flag to sandbox mode satisfies both.

The real `factory agent` CLI is invoked twice against a fake `claude` on PATH that records its
argv. The two runs differ in exactly one environment variable, which is reported so a judge can
confirm the distinction was actually exercised.
"""

from __future__ import annotations

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

CLAUDE_SHIM = """#!/usr/bin/env bash
# Fake Claude Code. Records argv, then emits one stream-json result line so the runner's parser is
# satisfied and the invocation completes normally.
set -u
state="${SHIM_STATE:?SHIM_STATE must be set}"
case_name="${FACTORY_EVAL_CASE:-unknown}"
out="$state/claude_argv_${case_name}.txt"
: > "$out"
for a in "$@"; do printf '%s\\n' "$a" >> "$out"; done
printf '%s\\n' '{"type":"result","subtype":"success","result":"probe-ok","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0,"num_turns":1}'
exit 0
"""

BASE_ENV = {
    "PATH": "/tmp/factory-eval-contained/shim:/usr/bin:/bin",
    "HOME": "/tmp/factory-eval-contained/home",
    "SHIM_STATE": "/tmp/factory-eval-contained/state",
    "LANG": "C",
    "FACTORY_RUNNER": "claude",
    # Keep telemetry and GitHub out of the picture so the run is hermetic.
    "FACTORY_NO_GITHUB": "1",
}

# The single variable that distinguishes an in-sandbox factory process from an ordinary one.
SANDBOX_VAR = "FACTORY_SANDBOX"

CASES = {
    "sandbox": {SANDBOX_VAR: "1"},
    "normal": {},
}


def main() -> int:
    if not factory_bin():
        note("t1_claude_bare: no factory entry point")
        return 1

    project = fresh_dir("bare_project")
    shim_dir = fresh_dir("shim")
    state = fresh_dir("state")
    fresh_dir("home")
    write_executable(shim_dir / "claude", CLAUDE_SHIM)

    captures: dict[str, dict[str, object]] = {}
    argvs: dict[str, list[str] | None] = {}

    for case, extra in CASES.items():
        env = dict(BASE_ENV)
        env["FACTORY_EVAL_CASE"] = case
        env.update(extra)
        cmd = factory_bin() + [
            "agent",
            "researcher",
            "--task",
            "probe: compose the claude command line",
            "--project",
            str(project),
            "--timeout",
            "60",
        ]
        capture = run(cmd, env=env, cwd=project, timeout=180.0)
        argv_path = state / f"claude_argv_{case}.txt"
        argv = argv_path.read_text().splitlines() if argv_path.exists() else None
        capture["claude_argv"] = argv
        capture["case"] = case
        captures[case] = capture
        argvs[case] = argv

    env_diff = {
        "only_in_sandbox_case": sorted(set(CASES["sandbox"]) - set(CASES["normal"])),
        "only_in_normal_case": sorted(set(CASES["normal"]) - set(CASES["sandbox"])),
        "sandbox_marker": SANDBOX_VAR,
        "shared_env": dict(sorted(BASE_ENV.items())),
    }

    emit(
        probe_record(
            "C3",
            "t1",
            observations={
                "sandbox_mode_signalled_by": f"{SANDBOX_VAR}=1",
                "claude_argv": argvs["sandbox"],
                "argv_captured": argvs["sandbox"] is not None,
                "contains_bare": bool(argvs["sandbox"] and "--bare" in argvs["sandbox"]),
                "env_diff": env_diff,
            },
            invocations=[captures["sandbox"]],
        )
    )
    emit(
        probe_record(
            "C4",
            "t1",
            observations={
                "sandbox_mode_signalled_by": "nothing — ordinary invocation",
                "claude_argv": argvs["normal"],
                "argv_captured": argvs["normal"] is not None,
                "contains_bare": bool(argvs["normal"] and "--bare" in argvs["normal"]),
                "env_diff": env_diff,
            },
            invocations=[captures["normal"]],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
