#!/usr/bin/env python3
# COVERS: C22,C23,C24
"""C22/C23/C24 — the three guards on the `factory contained` command surface.

Each is about *when* and *how* something fails rather than whether it fails at all:

- C22 `--tmux-persist` must be refused while parsing. Refusing it later, after a sandbox exists and
  a project tree has been transferred, costs all of that work to reach the same conclusion.
- C23 a bare `--division` must be a parse error, never a silent inheritance of `--target`. A
  division that switches itself on because of an unrelated flag is the worst kind of surprise, since
  the division deliberately opens the isolation boundary.
- C24 missing growth context must warn and **continue**. Exit code 0 is the assertion; a non-zero
  exit here would turn a comparability caveat into an outage.

The exit code alone cannot distinguish a parse-time refusal from a runtime one, so the probe also
captures whether anything was provisioned or composed, and reports both for the judge to weigh.
"""

from __future__ import annotations

import json
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
)

BASE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp/factory-eval-contained/home",
    "LANG": "C",
    "FACTORY_OPENSHELL_DRY_RUN": "1",
}

GROWTH_VARS = ("FACTORY_MANAGED_DIRS", "FACTORY_VAULT_PATH")

# Markers that indicate the failure happened after parsing — i.e. work was done first.
RUNTIME_MARKERS = ("openshell sandbox", "Traceback", '"dry_run"', "sandbox create")


def main() -> int:
    if not factory_bin():
        note("t1_contained_guards: no factory entry point")
        return 1

    project = fresh_dir("guards_project")
    fresh_dir("home")
    (project / ".gitignore").write_text(".factory/\n")
    (project / ".factory").mkdir()
    (project / ".factory" / "config.json").write_text("{}\n")

    def invoke(extra: list[str], env: dict[str, str]) -> dict[str, object]:
        return run(
            factory_bin() + ["contained", str(project)] + extra,
            env=env,
            cwd=project,
            timeout=120.0,
        )

    # C22 — --tmux-persist
    tmux_capture = invoke(["--tmux-persist"], dict(BASE_ENV))
    tmux_output = str(tmux_capture.get("stderr", "")) + str(tmux_capture.get("stdout", ""))
    emit(
        probe_record(
            "C22",
            "t1",
            observations={
                "exit_code": tmux_capture.get("exit_code"),
                "stderr": tmux_capture.get("stderr"),
                "mentions_tmux": "tmux" in tmux_output.lower(),
                "argparse_error_prefix": "error:" in tmux_output,
                "runtime_markers_found": [m for m in RUNTIME_MARKERS if m in tmux_output],
                "note": (
                    "runtime_markers_found must be empty for a parse-time refusal: their presence "
                    "means a sandbox command was composed or run before the flag was rejected"
                ),
            },
            invocations=[tmux_capture],
        )
    )

    # C23 — bare --division, plus a control showing --target alone never enables a division
    bare_capture = invoke(["--division"], dict(BASE_ENV))
    bare_output = str(bare_capture.get("stderr", "")) + str(bare_capture.get("stdout", ""))
    target_only = invoke(["--target", "local"], dict(BASE_ENV))
    try:
        control_payload = json.loads(str(target_only.get("stdout", "")) or "{}")
    except json.JSONDecodeError:
        control_payload = {}
    # Checked by value, not by substring: the dry-run payload always carries the division keys, so
    # a text search finds the word "division" whether or not one was configured.
    control_division = {
        "driver_config": control_payload.get("driver_config"),
        "division_policy": control_payload.get("division_policy"),
        "build_pod": control_payload.get("build_pod"),
    }
    emit(
        probe_record(
            "C23",
            "t1",
            observations={
                "bare_division_exit_code": bare_capture.get("exit_code"),
                "bare_division_stderr": bare_capture.get("stderr"),
                "argparse_expected_one_argument": "expected one argument" in bare_output,
                "runtime_markers_found": [m for m in RUNTIME_MARKERS if m in bare_output],
                "control_target_local_exit_code": target_only.get("exit_code"),
                "control_division_config": control_division,
                "control_configured_a_division": any(v is not None for v in control_division.values()),
                "note": (
                    "the control run passes --target local with no --division; a division that "
                    "appeared there would be inheritance, which C23 forbids"
                ),
            },
            invocations=[bare_capture, target_only],
        )
    )

    # C24 — growth context absent must warn on stderr and still exit 0
    unset_env = {k: v for k, v in BASE_ENV.items() if k not in GROWTH_VARS}
    warn_capture = invoke([], unset_env)
    warn_stderr = str(warn_capture.get("stderr", ""))
    set_env = dict(BASE_ENV)
    set_env.update({v: f"/tmp/factory-eval-contained/{v.lower()}" for v in GROWTH_VARS})
    quiet_capture = invoke([], set_env)
    emit(
        probe_record(
            "C24",
            "t1",
            observations={
                "growth_vars_unset": list(GROWTH_VARS),
                "exit_code": warn_capture.get("exit_code"),
                "stderr": warn_stderr,
                "warning_names_each_var": {v: v in warn_stderr for v in GROWTH_VARS},
                "control_with_vars_set_exit_code": quiet_capture.get("exit_code"),
                "control_with_vars_set_warned": any(v in str(quiet_capture.get("stderr", "")) for v in GROWTH_VARS),
                "note": (
                    "exit_code must be 0 — this criterion is specifically that the factory warns "
                    "and continues. The control run has both variables set and should not warn, "
                    "which shows the warning is conditional rather than unconditional."
                ),
            },
            invocations=[warn_capture, quiet_capture],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
