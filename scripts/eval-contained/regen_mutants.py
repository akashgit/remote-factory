#!/usr/bin/env python3
"""Regenerate the mutation suite's patch files from explicit fault definitions.

Each mutant is a single, named fault. What matters and must stay stable is the *fault* — "drop
`--bare`", "widen the bind mount to $HOME" — not the surrounding diff context, which rots every time
the file it patches is edited. Keeping the faults declared here and the patches generated means a
refactor breaks the patches loudly (the meta-eval asserts every mutant still applies) and they are
re-anchored by rerunning this script rather than hand-edited.

    python3 scripts/eval-contained/regen_mutants.py            # write patches
    python3 scripts/eval-contained/regen_mutants.py --check     # verify anchors still match

This regenerates the *injected fault*, never the criterion. A criterion is only ever changed by
editing criteria.tsv and the rubric, which is a spec change requiring review (eval plan §7).
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MUTANTS_DIR = REPO_ROOT / "tests" / "eval_contained" / "mutants"


@dataclass(frozen=True)
class Mutant:
    """One injected fault and the criterion that must catch it."""

    name: str
    caught_by: str
    target: str
    anchor: str
    replacement: str
    rationale: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        name="M2_drop_bare",
        caught_by="C3",
        target="factory/runners/claude.py",
        anchor=(
            "        if in_sandbox():\n"
            "            # Inside an OpenShell sandbox there is no browser and no way to complete an OAuth\n"
            "            # login, so Claude Code must be told not to try. Scoped to sandbox mode: adding it\n"
            "            # unconditionally would change every ordinary invocation on a developer's machine.\n"
            '            cmd.append("--bare")\n'
        ),
        replacement="",
        rationale=(
            "Eval plan §6 M2. Without --bare, Claude Code attempts its OAuth login flow inside the "
            "sandbox and every agent turn hangs or fails on a prompt nothing can answer."
        ),
    ),
    Mutant(
        name="M14_bare_unconditional",
        caught_by="C4",
        target="factory/runners/claude.py",
        anchor="        if in_sandbox():\n            # Inside an OpenShell",
        replacement="        if True:  # MUTANT\n            # Inside an OpenShell",
        rationale=(
            "Addition beyond the eval plan's table, which has no mutant for C4. C4 exists to keep the "
            "runner change scoped; unscoped, --bare changes every invocation on a developer's machine, "
            "and C3 alone cannot tell the two apart."
        ),
    ),
    Mutant(
        name="M1_forward_claude_code_prefix",
        caught_by="C1",
        target="factory/cli/_run_args.py",
        anchor='    forward_prefixes=("FACTORY_",),\n    drop_prefixes=("FACTORY_EVAL_",),',
        replacement='    forward_prefixes=("FACTORY_", "CLAUDE_CODE_", "CLOUD_ML_"),\n    drop_prefixes=("FACTORY_EVAL_",),',
        rationale=(
            "Eval plan §6 M1. Re-adding the CLAUDE_CODE_ prefix sends CLAUDE_CODE_USE_VERTEX and "
            "CLOUD_ML_REGION into the sandbox, where Claude Code dials Vertex directly and the "
            "egress policy denies it — an error that reads as an infrastructure fault."
        ),
    ),
    Mutant(
        name="M3_base_url_v1_suffix",
        caught_by="C2",
        target="factory/sandbox.py",
        anchor='SANDBOX_INFERENCE_BASE_URL = "https://inference.local"',
        replacement='SANDBOX_INFERENCE_BASE_URL = "https://inference.local/v1"',
        rationale=(
            "Eval plan §6 M3. Claude Code appends /v1/messages itself, so a /v1 suffix produces "
            "/v1/v1/messages — a 404 that looks like a gateway problem."
        ),
    ),
    Mutant(
        name="M4_factory_state_respects_gitignore",
        caught_by="C5",
        target="factory/cli/contained.py",
        anchor="                local=factory_dir,\n                dest=sbx_path,\n                respect_gitignore=False,",
        replacement="                local=factory_dir,\n                dest=sbx_path,\n                respect_gitignore=True,",
        rationale=(
            "Eval plan §6 M4. Transferring .factory/ without disabling .gitignore filtering drops "
            "config.json, eval_profile.json, and results.tsv, and the factory boots into a "
            "fresh-project state without erroring."
        ),
    ),
    Mutant(
        name="M11_growth_absence_is_fatal",
        caught_by="C24",
        target="factory/cli/contained.py",
        anchor='    for warning in plan.warnings:\n        print(f"Warning: {warning}", file=sys.stderr)',
        replacement='    for warning in plan.warnings:\n        print(f"Warning: {warning}", file=sys.stderr)\n        return 1',
        rationale=(
            "Eval plan §6 M11. Turning a comparability caveat into a non-zero exit converts a "
            "warning into an outage; C24 exists to pin the exit code at 0."
        ),
    ),
    Mutant(
        name="M16_tmux_persist_rejected_late",
        caught_by="C22",
        target="factory/cli/contained.py",
        anchor='    p.add_argument("--tmux-persist", action=_RejectTmuxPersist, help=argparse.SUPPRESS)',
        replacement='    p.add_argument("--tmux-persist", action="store_true", default=False, help=argparse.SUPPRESS)',
        rationale=(
            "Addition beyond the eval plan's table, which has no mutant for C22. Accepting the flag "
            "at parse time and failing later is the exact defect C22 forbids, and an exit-code-only "
            "check cannot tell the two apart."
        ),
    ),
    Mutant(
        name="M17_credentials_forwarded",
        caught_by="C1",
        target="factory/cli/_run_args.py",
        anchor='    forward_prefixes=("FACTORY_",),\n    drop_prefixes=("FACTORY_EVAL_",),\n    drop_keys=(SANDBOX_DRY_RUN_ENV,),',
        replacement='    forward_prefixes=("FACTORY_", "ANTHROPIC_", "OPENAI_", "CODEX_"),\n    drop_prefixes=("FACTORY_EVAL_",),\n    drop_keys=(SANDBOX_DRY_RUN_ENV,),',
        rationale=(
            "Addition beyond the eval plan's table. Forwarding credential prefixes puts live API "
            "keys into the sandbox argv and into any dry-run output or retained evidence file. "
            "Restoring the ANTHROPIC_ prefix also lets a host ANTHROPIC_VERTEX_PROJECT_ID through, "
            "which C1 must catch."
        ),
    ),
    Mutant(
        name="M5_bind_mount_widened_to_home",
        caught_by="C8",
        target="factory/division.py",
        anchor="    resolved = str(project_path)",
        replacement='    resolved = str(Path.home())  # MUTANT',
        rationale=(
            "Eval plan §6 M5. Widening the bind mount to $HOME hands the division the rest of the "
            "user's filesystem while everything continues to work, so nothing surfaces it but this "
            "criterion."
        ),
    ),
    Mutant(
        name="M6_privileged_build_pod",
        caught_by="C11",
        target="factory/division.py",
        anchor='                        "privileged": False,\n                        "allowPrivilegeEscalation": False,',
        replacement='                        "privileged": True,  # MUTANT\n                        "allowPrivilegeEscalation": False,',
        rationale=(
            "Eval plan §6 M6. A privileged build pod still builds, so only an explicit check "
            "notices that the grant is far wider than the buildah rootless recipe needs."
        ),
    ),
    Mutant(
        name="M7_buildah_isolation_oci",
        caught_by="C12",
        target="factory/division.py",
        anchor='{"name": "BUILDAH_ISOLATION", "value": "chroot"}',
        replacement='{"name": "BUILDAH_ISOLATION", "value": "oci"}',
        rationale=(
            "Eval plan §6 M7. `oci` isolation sends buildah through runc and therefore "
            "CLONE_NEWUSER, which is exactly what the sandbox's seccomp filter denies."
        ),
    ),
    Mutant(
        name="M9_wildcard_mcp_rule",
        caught_by="C18",
        target="factory/division.py",
        anchor='LOCAL_DIVISION_TOOLS = (\n    "image_build",',
        replacement='LOCAL_DIVISION_TOOLS = (\n    "*",\n    "image_build",',
        rationale=(
            "Eval plan §6 M9. MCP policy cannot inspect arguments, so the tool name is the entire "
            "enforcement surface; a wildcard turns the allowlist into no boundary at all."
        ),
    ),
    Mutant(
        name="M18_pod_patch_replaces_containers",
        caught_by="C13",
        target="factory/division.py",
        anchor="        elif isinstance(base_value, list) and isinstance(patch_value, list):\n            result[key] = _merge_named_list(base_value, patch_value)",
        replacement="        elif isinstance(base_value, list) and isinstance(patch_value, list):\n            result[key] = copy.deepcopy(patch_value)  # MUTANT",
        rationale=(
            "Addition beyond the eval plan's table, which has no mutant for C13. Replacing lists "
            "instead of merging them makes a one-field patch silently discard the container's "
            "image, command, and security context — C11 and C12 would then be vacuously true "
            "because the fields they check no longer exist."
        ),
    ),
    Mutant(
        name="M19_privileged_override_silent",
        caught_by="C14",
        target="factory/division.py",
        anchor='    if "privileged: true" in blob:\n        findings.append("privileged: true is set")',
        replacement="    # MUTANT: privileged overrides are accepted silently",
        rationale=(
            "Addition beyond the eval plan's table, which has no mutant for C14. `--pod-manifest` "
            "is an explicit override, so it must be honoured — but honouring it silently means an "
            "operator can widen the privilege budget without anything saying so."
        ),
    ),
    Mutant(
        name="M15_tmux_argv_drift",
        caught_by="C21",
        target="factory/cli/_run_args.py",
        anchor='    if getattr(args, "no_github", False):\n        parts.append("--no-github")\n',
        replacement='    if getattr(args, "no_github", False):\n        parts.append("--no-gh")\n',
        rationale=(
            "Addition beyond the eval plan's table, which has no mutant for C21. The arg-builder "
            "extraction is a pure refactor whose only guard is byte-identity; a probe that cannot see a "
            "one-flag drift would let the refactor silently change what `factory tmux` runs."
        ),
    ),
)


def build_patch(mutant: Mutant) -> str:
    path = REPO_ROOT / mutant.target
    original = path.read_text()
    if mutant.anchor not in original:
        raise SystemExit(
            f"{mutant.name}: anchor not found in {mutant.target}.\n"
            f"The source has moved. Update this mutant's anchor to inject the same fault "
            f"({mutant.rationale})"
        )
    if original.count(mutant.anchor) != 1:
        raise SystemExit(
            f"{mutant.name}: anchor matches {original.count(mutant.anchor)} times in "
            f"{mutant.target}; it must match exactly once"
        )
    mutated = original.replace(mutant.anchor, mutant.replacement, 1)
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        mutated.splitlines(keepends=True),
        fromfile=f"a/{mutant.target}",
        tofile=f"b/{mutant.target}",
        n=3,
    )
    header = (
        f"# Mutant {mutant.name} — must be caught by {mutant.caught_by}\n"
        f"# {mutant.rationale}\n"
        f"# Generated by scripts/eval-contained/regen_mutants.py; edit the fault there, not here.\n"
    )
    return header + "".join(diff)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every anchor still matches and every patch file is current; write nothing",
    )
    args = parser.parse_args(argv)

    MUTANTS_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for mutant in MUTANTS:
        patch = build_patch(mutant)
        dest = MUTANTS_DIR / f"{mutant.name}.patch"
        if args.check:
            if not dest.exists() or dest.read_text() != patch:
                stale.append(mutant.name)
        else:
            dest.write_text(patch)
            print(f"wrote {dest.relative_to(REPO_ROOT)}  (caught by {mutant.caught_by})")

    if stale:
        print(
            "stale mutant patches: " + ", ".join(stale) + "\nrerun without --check to regenerate",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
