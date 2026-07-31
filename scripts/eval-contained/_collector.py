#!/usr/bin/env python3
"""Evidence collection for `factory contained` — the deterministic half of the evaluation.

Reads ``criteria.tsv``, works out which tiers this machine can actually run, dispatches the
probes whose criteria are runnable, and writes an evidence stream. Every criterion in the
manifest produces exactly one record: a probe record when it ran, a skip record when its tier
was unavailable, a not-applicable record when it belongs to a later implementation phase, or an
error record when its probe crashed or does not exist yet.

Nothing here decides pass or fail. Probes report what they observed; the judge applies the
rubric. Keeping the decision out of this file is the whole point (eval plan §2).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1

ALL_TIERS = ("t0", "t1", "t2", "t3")

# OpenShell is alpha with an explicitly unstable surface (spec §11). Pin it and record the pin in
# every evidence file so a t2/t3 failure can be attributed to the runtime, not the implementation.
# Kept in step with factory.openshell.PINNED_VERSION, duplicated rather than imported so the
# collector stays runnable without the factory package installed.
OPENSHELL_PINNED_VERSION = "0.0.92"


@dataclass(frozen=True)
class Criterion:
    """One row of criteria.tsv."""

    id: str
    tier_expr: str
    phase: int
    weight: float
    prop: str
    pass_condition: str

    @property
    def tiers(self) -> tuple[str, ...]:
        return tuple(t for t in self.tier_expr.replace("|", "+").split("+") if t)

    @property
    def requires_all_tiers(self) -> bool:
        """`t0+t2` needs both tiers; `t2|t3` needs either."""
        return "|" not in self.tier_expr

    def runnable(self, available: set[str]) -> bool:
        if self.requires_all_tiers:
            return all(t in available for t in self.tiers)
        return any(t in available for t in self.tiers)

    def missing(self, available: set[str]) -> list[str]:
        return [t for t in self.tiers if t not in available]


@dataclass
class Probe:
    """An executable probe and the criteria it covers."""

    path: Path
    covers: tuple[str, ...]
    tier: str

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class TierStatus:
    available: bool
    reason: str = ""
    facts: dict[str, object] = field(default_factory=dict)


def load_criteria(path: Path) -> list[Criterion]:
    """Parse criteria.tsv, skipping comment and blank lines."""
    criteria: list[Criterion] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 6:
            raise ValueError(
                f"{path}:{lineno}: expected 6 tab-separated fields, got {len(fields)}: {raw!r}"
            )
        cid, tier_expr, phase, weight, prop, cond = (f.strip() for f in fields)
        criteria.append(
            Criterion(
                id=cid,
                tier_expr=tier_expr,
                phase=int(phase),
                weight=float(weight),
                prop=prop,
                pass_condition=cond,
            )
        )
    if not criteria:
        raise ValueError(f"{path}: no criteria found")
    return criteria


def discover_probes(probes_dir: Path) -> list[Probe]:
    """Find probes and read their `# COVERS:` declaration."""
    probes: list[Probe] = []
    if not probes_dir.is_dir():
        return probes
    for path in sorted(probes_dir.iterdir()):
        if path.name.startswith("_") or not path.is_file():
            continue
        if path.suffix not in (".py", ".sh"):
            continue
        covers: tuple[str, ...] = ()
        for line in path.read_text().splitlines()[:20]:
            if "COVERS:" in line:
                covers = tuple(
                    c.strip() for c in line.split("COVERS:", 1)[1].split(",") if c.strip()
                )
                break
        if not covers:
            continue
        tier = path.name.split("_", 1)[0]
        probes.append(Probe(path=path, covers=covers, tier=tier))
    return probes


def _run(cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run a command, never raising. Used only for environment probing."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=str(exc))


def resolve_factory_bin(repo_root: Path) -> tuple[list[str], str]:
    """Locate the `factory` entry point, preferring the repo virtualenv."""
    venv = os.environ.get("VIRTUAL_ENV")
    candidates: list[Path] = []
    if venv:
        candidates.append(Path(venv) / "bin" / "factory")
    candidates.append(repo_root / ".venv" / "bin" / "factory")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return [str(cand)], "venv"
    on_path = shutil.which("factory")
    if on_path:
        return [on_path], "path"
    if shutil.which("uv"):
        return ["uv", "run", "--project", str(repo_root), "factory"], "uv-run"
    return [], "absent"


def openshell_version() -> str:
    """Return the installed OpenShell version, or the literal `absent`."""
    if not shutil.which("openshell"):
        return "absent"
    res = _run(["openshell", "--version"])
    text = (res.stdout or res.stderr).strip()
    return text.splitlines()[0] if text else "unknown"


def detect_tiers(repo_root: Path, factory_bin: list[str]) -> dict[str, TierStatus]:
    """Work out which tiers this machine can run, with a reason for every unavailable one."""
    status: dict[str, TierStatus] = {}

    if factory_bin:
        static = TierStatus(available=True, facts={"factory_bin": " ".join(factory_bin)})
    else:
        static = TierStatus(available=False, reason="the `factory` entry point is not installed")
    status["t0"] = static
    status["t1"] = TierStatus(available=static.available, reason=static.reason, facts=dict(static.facts))

    # t2 — a real OpenShell sandbox on this machine. Every one of these is load-bearing: the binary
    # alone provisions nothing, and a registered gateway that does not answer provisions nothing
    # either. Accepting a partial environment here is the "absent-dependency pass" the whole tier
    # model exists to prevent — probes would run against a dead gateway and report failures that
    # belong to the machine rather than to the implementation.
    if not shutil.which("openshell"):
        status["t2"] = TierStatus(available=False, reason="openshell is not installed")
    else:
        engine_facts: dict[str, object] = {"openshell_version": openshell_version()}
        podman_state = ""
        if shutil.which("podman"):
            res = _run(["podman", "machine", "inspect", "--format", "{{.State}}"])
            podman_state = res.stdout.strip()
            engine_facts["podman_machine_state"] = podman_state or res.stderr.strip()[:200]
        docker_up = False
        if shutil.which("docker"):
            docker_up = _run(["docker", "info", "--format", "{{.ServerVersion}}"]).returncode == 0
            engine_facts["docker_up"] = docker_up
        engine_up = podman_state == "running" or docker_up

        gateways: list[object] = []
        gw = _run(["openshell", "gateway", "list", "-o", "json"])
        if gw.returncode == 0:
            try:
                parsed = json.loads(gw.stdout or "[]")
                gateways = parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                gateways = []
        engine_facts["registered_gateways"] = len(gateways)

        gateway_reachable = False
        if gateways:
            probe = _run(["openshell", "sandbox", "list", "-o", "json"])
            gateway_reachable = probe.returncode == 0
            engine_facts["gateway_probe_exit_code"] = probe.returncode
            if not gateway_reachable:
                engine_facts["gateway_probe_stderr"] = (probe.stderr or "").strip()[:300]

        if not engine_up:
            status["t2"] = TierStatus(
                available=False,
                reason="no running container engine (podman machine is not running, docker is down)",
                facts=engine_facts,
            )
        elif not gateways:
            status["t2"] = TierStatus(
                available=False,
                reason="no OpenShell gateway is registered (`openshell gateway add ...`)",
                facts=engine_facts,
            )
        elif not gateway_reachable:
            status["t2"] = TierStatus(
                available=False,
                reason="the registered OpenShell gateway did not answer a sandbox listing",
                facts=engine_facts,
            )
        else:
            status["t2"] = TierStatus(available=True, facts=engine_facts)

    # t3 — a reachable cluster.
    kube_bin = shutil.which("oc") or shutil.which("kubectl")
    if not kube_bin:
        status["t3"] = TierStatus(available=False, reason="neither oc nor kubectl is installed")
    else:
        whoami = _run([kube_bin, "whoami"] if kube_bin.endswith("oc") else [kube_bin, "auth", "whoami"])
        if whoami.returncode == 0:
            ns = _run([kube_bin, "config", "view", "--minify", "-o", "jsonpath={..namespace}"])
            nodes = _run(
                [kube_bin, "get", "nodes", "-o", "jsonpath={.items[*].status.nodeInfo.architecture}"]
            )
            status["t3"] = TierStatus(
                available=True,
                facts={
                    "kube_bin": kube_bin,
                    "user": whoami.stdout.strip(),
                    "namespace": ns.stdout.strip(),
                    "node_architectures": sorted(set(nodes.stdout.split())),
                },
            )
        else:
            status["t3"] = TierStatus(
                available=False,
                reason=f"no cluster session ({whoami.stderr.strip()[:160] or 'whoami failed'})",
            )
    return status


def meta_record(repo_root: Path, factory_bin: list[str], bin_source: str) -> dict[str, object]:
    head = _run(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    dirty = _run(["git", "-C", str(repo_root), "status", "--porcelain"])
    branch = _run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
    versions: dict[str, str] = {"openshell": openshell_version()}
    for tool, cmd in (
        ("podman", ["podman", "--version"]),
        # Plain output, not `-o json`: the JSON form is pretty-printed, so taking its first line
        # records the literal "{" as the version.
        ("oc", ["oc", "version", "--client=true"]),
        ("claude", ["claude", "--version"]),
        ("uv", ["uv", "--version"]),
    ):
        if shutil.which(tool):
            res = _run(cmd)
            out = (res.stdout or res.stderr).strip()
            versions[tool] = out.splitlines()[0][:200] if out else "unknown"
        else:
            versions[tool] = "absent"
    return {
        "record": "meta",
        "schema": SCHEMA_VERSION,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": head.stdout.strip(),
        "git_branch": branch.stdout.strip(),
        "git_dirty": bool(dirty.stdout.strip()),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "factory_bin": " ".join(factory_bin),
        "factory_bin_source": bin_source,
        "openshell_version": versions["openshell"],
        "openshell_pinned_version": OPENSHELL_PINNED_VERSION,
        "tool_versions": versions,
    }


def coverage_record(
    criteria: list[Criterion],
    requested: list[str],
    tiers: dict[str, TierStatus],
    phase: int,
) -> dict[str, object]:
    available = {t for t in requested if tiers[t].available}
    ran = sorted(available)
    skipped = [
        {"tier": t, "reason": tiers[t].reason}
        for t in requested
        if not tiers[t].available
    ]
    not_requested = [t for t in ALL_TIERS if t not in requested]
    return {
        "record": "coverage",
        "schema": SCHEMA_VERSION,
        "phase": phase,
        "tiers_requested": list(requested),
        "tiers_ran": ran,
        "tiers_skipped": skipped
        + [{"tier": t, "reason": "not requested for this run"} for t in not_requested],
        "tier_facts": {t: tiers[t].facts for t in ALL_TIERS},
        "criteria": [
            {
                "id": c.id,
                "tier": c.tier_expr,
                "phase": c.phase,
                "weight": c.weight,
                "property": c.prop,
                "pass_condition": c.pass_condition,
            }
            for c in criteria
        ],
    }


def _decode(raw: bytes, limit: int = 200_000) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} bytes]"
    return text


def run_probe(
    probe: Probe,
    repo_root: Path,
    factory_bin: list[str],
    timeout: float,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    """Execute a probe and parse its JSONL output.

    Returns (records, error_record). A probe is expected to emit one JSON object per line on
    stdout. Anything it writes that is not parseable JSON is preserved in the error record so a
    broken probe is visible rather than silently dropping criteria.
    """
    env = dict(os.environ)
    env["FACTORY_EVAL_REPO_ROOT"] = str(repo_root)
    env["FACTORY_EVAL_FACTORY_BIN"] = json.dumps(factory_bin)
    env.setdefault("FACTORY_EVAL_EXTRA_ENV", os.environ.get("FACTORY_EVAL_EXTRA_ENV", ""))
    cmd = [sys.executable, str(probe.path)] if probe.path.suffix == ".py" else ["bash", str(probe.path)]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False, cwd=str(repo_root), env=env
        )
    except subprocess.TimeoutExpired as exc:
        return [], {
            "record": "error",
            "probe": probe.name,
            "criteria": list(probe.covers),
            "reason": f"probe timed out after {timeout}s",
            "stdout": _decode(exc.stdout or b""),
            "stderr": _decode(exc.stderr or b""),
        }

    records: list[dict[str, object]] = []
    unparsed: list[str] = []
    for line in _decode(proc.stdout).splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            unparsed.append(line)
            continue
        if isinstance(parsed, dict) and parsed.get("record") == "probe":
            parsed.setdefault("probe", probe.name)
            parsed["duration_s"] = round(time.time() - started, 3)
            records.append(parsed)
        else:
            unparsed.append(line)

    error: dict[str, object] | None = None
    covered = {str(r.get("id")) for r in records}
    if proc.returncode != 0 or unparsed or set(probe.covers) - covered:
        error = {
            "record": "error",
            "probe": probe.name,
            "criteria": sorted(set(probe.covers) - covered),
            "reason": (
                f"probe exited {proc.returncode}"
                if proc.returncode != 0
                else "probe emitted no record for some criteria it declares"
            ),
            "unparsed_stdout": unparsed[:50],
            "stderr": _decode(proc.stderr),
        }
        if not error["criteria"] and proc.returncode == 0:
            error = None
    return records, error


def collect(
    repo_root: Path,
    requested_tiers: list[str],
    phase: int,
    probe_timeout: float,
) -> list[dict[str, object]]:
    """Produce the full evidence stream as a list of records."""
    here = Path(__file__).resolve().parent
    criteria = load_criteria(here / "criteria.tsv")
    probes = discover_probes(here / "probes")
    factory_bin, bin_source = resolve_factory_bin(repo_root)
    tiers = detect_tiers(repo_root, factory_bin)
    available = {t for t in requested_tiers if tiers[t].available}

    out: list[dict[str, object]] = [
        meta_record(repo_root, factory_bin, bin_source),
        coverage_record(criteria, requested_tiers, tiers, phase),
    ]

    by_id = {c.id: c for c in criteria}
    pending = {c.id for c in criteria}
    # Criteria closed out by phase or tier gating before any probe ran. A probe may still emit a
    # record for one of these — a probe covering both C9 and C8 has no way to know C8 was skipped —
    # and that record must be dropped quietly rather than reported as a duplicate.
    gated: set[str] = set()

    # Phase gating first: a criterion from a later phase is not applicable, not failed.
    for c in criteria:
        if c.phase > phase:
            out.append(
                {
                    "record": "probe",
                    "id": c.id,
                    "tier": c.tier_expr,
                    "status": "not_applicable",
                    "phase": c.phase,
                    "judged_phase": phase,
                    "reason": f"introduced by implementation phase {c.phase}; judging phase {phase}",
                }
            )
            pending.discard(c.id)
            gated.add(c.id)

    # Then tier gating.
    for c in criteria:
        if c.id not in pending:
            continue
        if not c.runnable(available):
            missing = c.missing(available)
            reasons = "; ".join(
                f"{t}: {tiers[t].reason or 'unavailable'}" for t in missing if t in tiers
            )
            out.append(
                {
                    "record": "probe",
                    "id": c.id,
                    "tier": c.tier_expr,
                    "status": "skipped",
                    "reason": f"tier unavailable — {reasons}",
                }
            )
            pending.discard(c.id)
            gated.add(c.id)

    # Run each probe whose declared criteria are all still pending.
    for probe in probes:
        targets = [cid for cid in probe.covers if cid in pending]
        if not targets:
            continue
        unknown = [cid for cid in probe.covers if cid not in by_id]
        if unknown:
            out.append(
                {
                    "record": "error",
                    "probe": probe.name,
                    "criteria": unknown,
                    "reason": "probe declares criteria that are not in criteria.tsv",
                }
            )
        records, error = run_probe(probe, repo_root, factory_bin, probe_timeout)
        for rec in records:
            cid = str(rec.get("id"))
            if cid in pending:
                pending.discard(cid)
                out.append(rec)
            elif cid in gated:
                # Settled by phase or tier gating before the probe ran. Expected, not an error.
                continue
            elif cid in by_id:
                # Two probes emitting for the same criterion. Ambiguous evidence, so it is reported
                # rather than resolved by arrival order.
                out.append(
                    {
                        "record": "error",
                        "probe": probe.name,
                        "criteria": [cid],
                        "reason": "duplicate record for a criterion already reported by another probe",
                    }
                )
        if error:
            out.append(error)
            for cid in error.get("criteria", []):  # type: ignore[union-attr]
                pending.discard(str(cid))

    # Anything left has no probe at all. Say so loudly; the judge must not pass it.
    for cid in sorted(pending):
        out.append(
            {
                "record": "error",
                "probe": None,
                "criteria": [cid],
                "reason": "no probe implements this criterion",
            }
        )
    return out


def validate_evidence(records: list[dict[str, object]]) -> list[str]:
    """Structural checks on an evidence stream. Returns a list of violations.

    This enforces the skip semantics the whole evaluation rests on (eval plan §3): a skipped tier
    is never reported as a pass, and every criterion is accounted for exactly once. It is used by
    the meta-eval to catch an evidence stream that lies about its own coverage.
    """
    problems: list[str] = []
    coverage = [r for r in records if r.get("record") == "coverage"]
    if len(coverage) != 1:
        problems.append(f"expected exactly 1 coverage record, found {len(coverage)}")
        return problems
    cov = coverage[0]
    declared = {str(c["id"]) for c in cov.get("criteria", [])}  # type: ignore[union-attr,index]

    seen: dict[str, list[dict[str, object]]] = {}
    for rec in records:
        if rec.get("record") == "probe":
            seen.setdefault(str(rec.get("id")), []).append(rec)
        elif rec.get("record") == "error":
            for cid in rec.get("criteria") or []:
                seen.setdefault(str(cid), []).append(rec)

    for cid in sorted(declared):
        got = seen.get(cid, [])
        if not got:
            problems.append(f"{cid}: declared in coverage but has no record")
        elif len(got) > 1:
            problems.append(f"{cid}: {len(got)} records, expected 1")
    for cid in sorted(set(seen) - declared):
        problems.append(f"{cid}: record present but not declared in coverage")

    ran = set(cov.get("tiers_ran", []))  # type: ignore[arg-type]
    for rec in records:
        if rec.get("record") != "probe":
            continue
        status = rec.get("status")
        if status not in ("ok", "skipped", "not_applicable"):
            problems.append(f"{rec.get('id')}: unknown status {status!r}")
        if status == "skipped" and not str(rec.get("reason", "")).strip():
            problems.append(f"{rec.get('id')}: skipped without a reason")
        if status == "ok":
            tier_expr = str(rec.get("tier", ""))
            tiers = [t for t in tier_expr.replace("|", "+").split("+") if t]
            need_all = "|" not in tier_expr
            satisfied = all(t in ran for t in tiers) if need_all else any(t in ran for t in tiers)
            if tiers and not satisfied:
                problems.append(
                    f"{rec.get('id')}: reported status ok on tier {tier_expr} "
                    f"but tiers_ran is {sorted(ran)}"
                )
            if "command" not in rec and "observations" not in rec:
                problems.append(f"{rec.get('id')}: status ok with neither command nor observations")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect evidence for the `factory contained` evaluation.",
    )
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--tiers",
        default="t0,t1",
        help="Comma-separated tiers to attempt (default: t0,t1). Unavailable tiers are skipped, "
             "never silently dropped.",
    )
    parser.add_argument(
        "--phase", type=int, default=1, help="Implementation phase under judgement (spec §13)"
    )
    parser.add_argument("--out", default="-", help="Output file, or - for stdout")
    parser.add_argument(
        "--probe-timeout", type=float, default=300.0, help="Per-probe timeout in seconds"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Check the produced stream's structure and exit non-zero on a violation",
    )
    args = parser.parse_args(argv)

    requested = [t.strip() for t in args.tiers.split(",") if t.strip()]
    bad = [t for t in requested if t not in ALL_TIERS]
    if bad:
        parser.error(f"unknown tier(s): {', '.join(bad)}; valid tiers are {', '.join(ALL_TIERS)}")

    repo_root = Path(args.repo_root).resolve()
    records = collect(repo_root, requested, args.phase, args.probe_timeout)

    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text)

    if args.validate:
        problems = validate_evidence(records)
        for p in problems:
            print(f"evidence violation: {p}", file=sys.stderr)
        if problems:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
