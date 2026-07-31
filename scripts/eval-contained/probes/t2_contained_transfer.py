#!/usr/bin/env python3
# COVERS: C5,C6,C7,C26
"""C5/C6/C7/C26 — what actually survives a real transfer into a real sandbox.

These cannot be proven statically, which is the whole reason they sit in t2. A dry-run shows that
the *right commands* were composed; only a live sandbox shows that `.factory/config.json` is on the
other side of them. The distinction matters because the failure mode here is silent: OpenShell
applies `.gitignore` filtering to uploads by default, this project's convention is to gitignore
`.factory/`, and a factory that arrives without its state boots into a fresh project and starts
scoring from zero without erroring.

The test project's `.gitignore` really does list `.factory/`, and `results.tsv` really does have
three rows. A probe against a project that does not gitignore `.factory/` would pass whether or not
the implementation handles the trap, and would therefore prove nothing.

Every sandbox created here is deleted in a finally block; a leaked sandbox holds a container.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
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

RESULTS_ROWS = 3

BASE_ENV = {
    "PATH": f"{os.environ.get('HOME', '')}/.local/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": os.environ.get("HOME", "/tmp"),
    "LANG": "C",
    "FACTORY_MANAGED_DIRS": "/tmp/factory-eval-contained/managed",
    "FACTORY_VAULT_PATH": "/tmp/factory-eval-contained/vault",
}


def _make_project(name: str) -> Path:
    """A project shaped like the trap: .factory/ present, and gitignored."""
    project = fresh_dir(name)
    subprocess.run(["git", "init", "-q", str(project)], check=False, capture_output=True)
    (project / ".gitignore").write_text(".factory/\n__pycache__/\n")
    (project / "main.py").write_text("print('hello')\n")
    state = project / ".factory"
    state.mkdir()
    (state / "config.json").write_text(json.dumps({"name": name, "runner": "claude"}) + "\n")
    (state / "eval_profile.json").write_text('{"dimensions": []}\n')
    (state / "results.tsv").write_text("".join(f"{i}\thypothesis-{i}\t0.5\n" for i in range(RESULTS_ROWS)))
    return project


def _sandbox_exec(name: str, argv: list[str]) -> dict[str, object]:
    return run(
        ["openshell", "sandbox", "exec", name, "--"] + argv,
        env=dict(BASE_ENV),
        cwd=Path("/tmp"),
        timeout=180.0,
    )


def _delete(name: str) -> None:
    run(["openshell", "sandbox", "delete", name], env=dict(BASE_ENV), cwd=Path("/tmp"), timeout=120.0)


def _sandbox_path(project: Path) -> str:
    return f"/sandbox/{project.name}"


def main() -> int:
    if not factory_bin():
        note("t2_contained_transfer: no factory entry point")
        return 1
    if shutil.which("openshell") is None:
        note("t2_contained_transfer: openshell absent — the collector should not have run this")
        return 1

    project = _make_project("t2_project")
    sbx_path = _sandbox_path(project)
    provision = run(
        factory_bin() + ["contained", str(project), "--mode", "improve", "--no-github"],
        env=dict(BASE_ENV),
        cwd=project,
        timeout=900.0,
    )
    sandbox = f"factory-{project.name}-"  # resolved from the listing below

    listing = run(
        ["openshell", "sandbox", "list", "--selector", "factory.name=t2_project", "-o", "json"],
        env=dict(BASE_ENV),
        cwd=Path("/tmp"),
        timeout=120.0,
    )
    try:
        entries = json.loads(str(listing.get("stdout", "[]")) or "[]")
    except json.JSONDecodeError:
        entries = []
    sandbox = entries[0].get("name", sandbox) if entries else sandbox

    try:
        # C5 — did the state arrive, and is it intact?
        config = _sandbox_exec(sandbox, ["cat", f"{sbx_path}/.factory/config.json"])
        rows = _sandbox_exec(sandbox, ["sh", "-lc", f"wc -l < {sbx_path}/.factory/results.tsv"])
        row_count_raw = str(rows.get("stdout", "")).strip()
        emit(
            probe_record(
                "C5",
                "t2",
                observations={
                    "gitignore_contents": (project / ".gitignore").read_text(),
                    "gitignore_lists_factory_dir": ".factory/" in (project / ".gitignore").read_text(),
                    "host_results_tsv_rows": RESULTS_ROWS,
                    "sandbox_results_tsv_rows_raw": row_count_raw,
                    "sandbox_config_json": str(config.get("stdout", "")).strip(),
                    "config_present": config.get("exit_code") == 0,
                    "provision_exit_code": provision.get("exit_code"),
                },
                invocations=[provision, config, rows],
            )
        )

        # C7 — the same project must be detected as the same state on both sides
        host_detect = run(
            factory_bin() + ["detect", str(project)], env=dict(BASE_ENV), cwd=project, timeout=120.0
        )
        sandbox_detect = _sandbox_exec(sandbox, ["factory", "detect", sbx_path])
        emit(
            probe_record(
                "C7",
                "t2",
                observations={
                    "host_stdout": str(host_detect.get("stdout", "")).strip(),
                    "sandbox_stdout": str(sandbox_detect.get("stdout", "")).strip(),
                    "identical": str(host_detect.get("stdout", "")).strip()
                    == str(sandbox_detect.get("stdout", "")).strip(),
                },
                invocations=[host_detect, sandbox_detect],
            )
        )

        # C26 — discoverable by label, with no session-mapping file written anywhere
        mapping_candidates = [
            Path("~/.factory/sandboxes.json").expanduser(),
            Path("~/.factory/openshell_sandboxes.json").expanduser(),
            project / ".factory" / "sandboxes.json",
        ]
        emit(
            probe_record(
                "C26",
                "t2",
                observations={
                    "selector_used": "factory.name=t2_project",
                    "listing_stdout": str(listing.get("stdout", "")),
                    "sandboxes_found": len(entries),
                    "resolved_sandbox_name": sandbox,
                    "mapping_files_written": [str(p) for p in mapping_candidates if p.exists()],
                },
                invocations=[listing],
            )
        )
    finally:
        _delete(sandbox)

    # C6 — a broken transfer must fail loudly, and must never boot a fresh project
    broken = _make_project("t2_broken")
    broken_state = broken / ".factory"
    original_mode = broken_state.stat().st_mode
    os.chmod(broken_state, 0o000)
    try:
        broken_run = run(
            factory_bin() + ["contained", str(broken), "--mode", "improve", "--no-github"],
            env=dict(BASE_ENV),
            cwd=broken,
            timeout=900.0,
        )
        combined = str(broken_run.get("stderr", "")) + str(broken_run.get("stdout", ""))
        emit(
            probe_record(
                "C6",
                "t2",
                observations={
                    "fault_injected": f"chmod 000 {broken_state}",
                    "exit_code": broken_run.get("exit_code"),
                    "stderr": broken_run.get("stderr"),
                    "message_names_factory_dir": ".factory" in combined,
                    "note": (
                        "a zero exit code here means the factory silently proceeded into a "
                        "fresh-project state, which is the failure this criterion exists to catch"
                    ),
                },
                invocations=[broken_run],
            )
        )
    finally:
        os.chmod(broken_state, stat.S_IMODE(original_mode))
        _delete(f"factory-{broken.name}-")

    return 0


if __name__ == "__main__":
    sys.exit(main())
