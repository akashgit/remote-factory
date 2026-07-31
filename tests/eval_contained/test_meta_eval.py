"""Meta-evaluation: who checks the checker.

An evaluation that passes everything is worthless, and a green result cannot distinguish "the
implementation is correct" from "the probes are blind". So every criterion is tested against a
known-broken implementation: apply a mutant that breaks exactly one thing, re-run the probe, and
assert the criterion can no longer pass. A mutant that survives means the criterion is decorative
(eval plan §6).

Two further checks guard the evaluation's own honesty rather than the implementation's: the skip
semantics (a tier that did not run can never be reported as passing — M13) and the weight manifest
(C19 outweighs everything else, and a weight is not a dial).

These tests spawn real `factory` subprocesses against fake `claude` and `tmux` binaries, so they are
slow and are marked `meta_eval`. They also use fixed paths under /tmp for determinism, which means
they must not run concurrently with a live evidence collection.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.eval_contained.criteria import judge

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "scripts" / "eval-contained"
PROBES_DIR = EVAL_DIR / "probes"
MUTANTS_DIR = Path(__file__).resolve().parent / "mutants"

pytestmark = pytest.mark.meta_eval


def _load_collector() -> Any:
    """Import the collector from its hyphenated directory."""
    spec = importlib.util.spec_from_file_location(
        "_contained_collector", EVAL_DIR / "_collector.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = _load_collector()


# Mutant → (criteria that must catch it, probe that produces their evidence).
#
# M2 and M13 come from the eval plan's table. M14 and M15 are additions: the plan's table has no
# mutant for C4 or C21, which left the two regression guards — "the flag is scoped" and "tmux is
# unchanged" — untested for sensitivity. Both are cheap, so the gap is closed here rather than
# recorded as a caveat.
MUTANTS: list[tuple[str, list[str], str]] = [
    # Phase 1
    ("M2_drop_bare.patch", ["C3"], "t1_claude_bare.py"),
    ("M14_bare_unconditional.patch", ["C4"], "t1_claude_bare.py"),
    ("M15_tmux_argv_drift.patch", ["C21"], "t1_tmux_golden.py"),
    # Phase 2
    ("M1_forward_claude_code_prefix.patch", ["C1"], "t1_contained_env.py"),
    ("M3_base_url_v1_suffix.patch", ["C2"], "t1_contained_env.py"),
    ("M11_growth_absence_is_fatal.patch", ["C24"], "t1_contained_guards.py"),
    ("M16_tmux_persist_rejected_late.patch", ["C22"], "t1_contained_guards.py"),
    ("M17_credentials_forwarded.patch", ["C1"], "t1_contained_env.py"),
    # Phase 3
    ("M6_privileged_build_pod.patch", ["C11"], "t1_division_local.py"),
    ("M7_buildah_isolation_oci.patch", ["C12"], "t1_division_local.py"),
    ("M9_wildcard_mcp_rule.patch", ["C18"], "t1_division_local.py"),
    ("M18_pod_patch_replaces_containers.patch", ["C13"], "t1_division_local.py"),
    ("M19_privileged_override_silent.patch", ["C14"], "t1_division_local.py"),
]

# M4 breaks the .factory/ transfer, which only a real sandbox can observe (C5 is t2). It is
# generated and kept current by the manifest check below, but cannot be exercised here.
# M4 and M5 break behaviour that only a live sandbox can observe (C5 is t2; C8 is t0+t2, so the
# collector skips it without t2). Both are generated and kept current by the manifest check, but
# cannot be exercised here.
T2_ONLY_MUTANTS = [
    ("M4_factory_state_respects_gitignore.patch", "C5"),
    ("M5_bind_mount_widened_to_home.patch", "C8"),
]

TESTABLE_CRITERIA = [
    "C1", "C2", "C3", "C4", "C9", "C10", "C11", "C12", "C13", "C14", "C18",
    "C21", "C22", "C23", "C24", "C25",
]
PROBES_BY_CRITERION = {
    "C1": "t1_contained_env.py",
    "C2": "t1_contained_env.py",
    "C3": "t1_claude_bare.py",
    "C4": "t1_claude_bare.py",
    "C21": "t1_tmux_golden.py",
    "C22": "t1_contained_guards.py",
    "C23": "t1_contained_guards.py",
    "C24": "t1_contained_guards.py",
    "C25": "t1_contained_env.py",
    "C9": "t1_division_local.py",
    "C10": "t1_division_local.py",
    "C11": "t1_division_local.py",
    "C12": "t1_division_local.py",
    "C13": "t1_division_local.py",
    "C14": "t1_division_local.py",
    "C18": "t1_division_local.py",
}


def _source_copy(dest: Path) -> Path:
    """Copy the implementation package so a mutant can be applied without touching the work tree."""
    shutil.copytree(
        REPO_ROOT / "factory",
        dest / "factory",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def _apply(patch: Path, workspace: Path) -> None:
    result = subprocess.run(
        ["patch", "-p1", "--forward", "-i", str(patch)],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"mutant {patch.name} did not apply — it has drifted from the source it mutates.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def _run_probe(probe: str, source_root: Path) -> dict[str, dict[str, Any]]:
    """Run one probe against a given source tree; return its records keyed by criterion id."""
    env = {
        "FACTORY_EVAL_REPO_ROOT": str(REPO_ROOT),
        "FACTORY_EVAL_FACTORY_BIN": json.dumps([sys.executable, "-m", "factory.cli"]),
        "FACTORY_EVAL_EXTRA_ENV": json.dumps({"PYTHONPATH": str(source_root)}),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
    }
    proc = subprocess.run(
        [sys.executable, str(PROBES_DIR / probe)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=600,
    )
    assert proc.returncode == 0, f"probe {probe} failed: {proc.stderr[-2000:]}"
    records: dict[str, dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        records[str(parsed["id"])] = parsed
    return records


@pytest.fixture(scope="module")
def baseline_records(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Probe records from the unmutated tree, collected through the same harness as the mutants."""
    workspace = _source_copy(tmp_path_factory.mktemp("baseline"))
    records: dict[str, dict[str, Any]] = {}
    for probe in sorted(set(PROBES_BY_CRITERION.values())):
        records.update(_run_probe(probe, workspace))
    return records


@pytest.mark.parametrize("criterion", TESTABLE_CRITERIA)
def test_baseline_criterion_passes(
    criterion: str, baseline_records: dict[str, dict[str, Any]]
) -> None:
    """The probes must pass on the real implementation.

    Without this, a mutant test proves only that the probe fails on everything.
    """
    assert criterion in baseline_records, f"no record for {criterion}"
    passed, reason = judge(criterion, baseline_records[criterion])
    assert passed, f"{criterion} fails on the unmutated tree: {reason}"


@pytest.mark.parametrize(
    ("patch_name", "criteria", "probe"), MUTANTS, ids=[m[0].split("_")[0] for m in MUTANTS]
)
def test_mutant_is_caught(
    patch_name: str, criteria: list[str], probe: str, tmp_path: Path
) -> None:
    """Each mutant must be caught by the criterion the plan assigns to it."""
    patch = MUTANTS_DIR / patch_name
    assert patch.exists(), f"missing mutant {patch_name}"
    workspace = _source_copy(tmp_path)
    _apply(patch, workspace)

    records = _run_probe(probe, workspace)
    for criterion in criteria:
        assert criterion in records, f"{patch_name}: no record for {criterion}"
        passed, reason = judge(criterion, records[criterion])
        assert not passed, (
            f"{patch_name} survived {criterion} — the criterion is decorative. "
            f"Oracle said: {reason}"
        )


def problems_of(stream: list[dict[str, Any]]) -> list[str]:
    return collector.validate_evidence(stream)


class TestSkipSemantics:
    """M13 — a tier that did not run can never be reported as having passed.

    This is the evaluation's own honesty check, and the plan requires it to hold before any other
    verdict is trusted. It is structural rather than mutational: the fault being injected is a lying
    evidence stream, not a broken implementation.
    """

    @staticmethod
    def _stream(*probes: dict[str, Any], tiers_ran: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "record": "coverage",
                "phase": 2,
                "tiers_requested": ["t0", "t1", "t2"],
                "tiers_ran": tiers_ran,
                "tiers_skipped": [{"tier": "t2", "reason": "openshell is not installed"}],
                "criteria": [{"id": p["id"], "tier": p["tier"], "phase": 2, "weight": 1.0} for p in probes],
            },
            *probes,
        ]

    def test_ok_status_on_a_tier_that_did_not_run_is_rejected(self) -> None:
        stream = self._stream(
            {
                "record": "probe",
                "id": "C5",
                "tier": "t2",
                "status": "ok",
                "command": ["openshell", "sandbox", "create"],
                "observations": {"config_json_present": True},
            },
            tiers_ran=["t0", "t1"],
        )
        problems = collector.validate_evidence(stream)
        assert any("tiers_ran" in p for p in problems), problems

    def test_honest_skip_is_accepted(self) -> None:
        stream = self._stream(
            {
                "record": "probe",
                "id": "C5",
                "tier": "t2",
                "status": "skipped",
                "reason": "tier unavailable — t2: openshell is not installed",
            },
            tiers_ran=["t0", "t1"],
        )
        assert collector.validate_evidence(stream) == []

    def test_skip_without_a_reason_is_rejected(self) -> None:
        stream = self._stream(
            {"record": "probe", "id": "C5", "tier": "t2", "status": "skipped", "reason": ""},
            tiers_ran=["t0", "t1"],
        )
        assert any("without a reason" in p for p in problems_of(stream)), problems_of(stream)

    def test_missing_record_is_rejected(self) -> None:
        stream = self._stream(
            {"record": "probe", "id": "C5", "tier": "t2", "status": "skipped", "reason": "no gateway"},
            tiers_ran=["t0", "t1"],
        )
        stream[0]["criteria"].append({"id": "C6", "tier": "t2", "phase": 2, "weight": 1.0})
        assert any("C6" in p and "no record" in p for p in problems_of(stream)), problems_of(stream)

    def test_duplicate_records_are_rejected(self) -> None:
        rec = {
            "record": "probe",
            "id": "C5",
            "tier": "t2",
            "status": "skipped",
            "reason": "no gateway",
        }
        stream = self._stream(rec, dict(rec), tiers_ran=["t0", "t1"])
        assert any("C5" in p and "records" in p for p in problems_of(stream)), problems_of(stream)

    def test_status_ok_without_captured_output_is_rejected(self) -> None:
        stream = self._stream(
            {"record": "probe", "id": "C5", "tier": "t1", "status": "ok"},
            tiers_ran=["t0", "t1"],
        )
        assert any("neither command nor observations" in p for p in problems_of(stream)), (
            problems_of(stream)
        )


class TestCriteriaManifest:
    """Guards on the manifest itself. A criterion that drifts out of the rubric stops being judged."""

    @staticmethod
    def _criteria() -> list[Any]:
        return collector.load_criteria(EVAL_DIR / "criteria.tsv")

    def test_ids_are_unique(self) -> None:
        ids = [c.id for c in self._criteria()]
        assert len(ids) == len(set(ids))

    def test_c19_outweighs_every_other_criterion(self) -> None:
        """Weight is not a dial (eval plan §7). C19 is the criterion that actually matters."""
        criteria = {c.id: c for c in self._criteria()}
        assert "C19" in criteria
        others = [c.weight for cid, c in criteria.items() if cid != "C19"]
        assert criteria["C19"].weight > max(others), (
            "C19 must outweigh every other criterion; lowering it is a spec change, not a fix"
        )

    def test_every_criterion_has_positive_weight(self) -> None:
        assert all(c.weight > 0 for c in self._criteria())

    def test_every_mutant_patch_is_current(self) -> None:
        """A mutant whose anchor has drifted silently stops testing anything."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "eval-contained" / "regen_mutants.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_t2_only_mutants_exist(self) -> None:
        for patch_name, _criterion in T2_ONLY_MUTANTS:
            assert (MUTANTS_DIR / patch_name).exists(), patch_name

    def test_every_criterion_appears_in_the_rubric(self) -> None:
        rubric = (
            REPO_ROOT / "docs" / "expected-behaviors" / "contained" / "verification-points.md"
        ).read_text()
        missing = [c.id for c in self._criteria() if f"| {c.id} |" not in rubric]
        assert not missing, f"criteria absent from the judge's rubric: {missing}"

    def test_every_implemented_criterion_has_an_oracle(self) -> None:
        from tests.eval_contained.criteria import ORACLE

        implemented = [c.id for c in self._criteria() if c.phase <= 3]
        missing = [cid for cid in implemented if cid not in ORACLE]
        # These need a live sandbox or cluster; nothing can judge them until one exists.
        assert missing == ["C5", "C6", "C7", "C8", "C19", "C26"], missing
        assert all(cid in ORACLE for cid in TESTABLE_CRITERIA)


def test_collection_accounts_for_every_criterion(tmp_path: Path) -> None:
    """A live collection must produce exactly one record per criterion — no silent drops."""
    records = collector.collect(REPO_ROOT, ["t0", "t1"], phase=1, probe_timeout=600.0)
    problems = collector.validate_evidence(records)
    assert problems == [], problems
    coverage = next(r for r in records if r["record"] == "coverage")
    assert {"t2", "t3"} <= {s["tier"] for s in coverage["tiers_skipped"]}, (
        "t2/t3 must be reported as skipped, never omitted"
    )
