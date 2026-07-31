"""Deterministic sensitivity oracle for the `factory contained` criteria.

**This is not the judge.** The judge is an agent that reads `evidence.jsonl` and the rubric and
never sees the implementation (eval plan §5). What lives here is a mechanical restatement of each
criterion's pass condition, used by the meta-evaluation for one purpose: to prove that a probe
actually *notices* when the corresponding behavior is broken.

That distinction matters. A criterion whose probe captures the same evidence whether the feature
works or not is decorative, and no amount of care in the judge can rescue it. The mutation suite
injects a known fault and this oracle asserts the evidence changed. If a mutant survives, the probe
is at fault and must be fixed before any verdict on the real implementation is worth reading.

Nothing in the evaluation pipeline imports this module. It exists only under `tests/`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Verdict = tuple[bool, str]


def _observations(record: dict[str, Any]) -> dict[str, Any]:
    obs = record.get("observations")
    return obs if isinstance(obs, dict) else {}


def _require_ok(record: dict[str, Any]) -> Verdict | None:
    """Shared preamble: a criterion cannot pass on a skipped, deferred, or crashed probe."""
    if record.get("record") == "error":
        return False, f"error record: {record.get('reason')}"
    status = record.get("status")
    if status == "skipped":
        return False, f"SKIPPED: {record.get('reason')}"
    if status == "not_applicable":
        return False, f"NOT_APPLICABLE: phase {record.get('phase')}"
    if status != "ok":
        return False, f"unknown status {status!r}"
    return None


def c3(record: dict[str, Any]) -> Verdict:
    """`--bare` present in the composed claude argv in sandbox mode."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    argv = obs.get("claude_argv")
    if not isinstance(argv, list) or not argv:
        return False, "no claude argv was captured, so nothing is proven"
    if "--bare" not in argv:
        return False, f"argv lacks --bare: {argv}"
    return True, f"argv contains --bare: {argv}"


def c4(record: dict[str, Any]) -> Verdict:
    """`--bare` absent from an ordinary, non-sandbox invocation."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    argv = obs.get("claude_argv")
    if not isinstance(argv, list) or not argv:
        return False, "no claude argv was captured, so nothing is proven"
    if "--bare" in argv:
        return False, f"--bare leaked into a non-sandbox invocation: {argv}"
    return True, "argv has no --bare outside sandbox mode"


def c21(record: dict[str, Any]) -> Verdict:
    """The composed `factory tmux` command is byte-identical to the recorded golden."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    missing = obs.get("missing_golden_for_cases") or []
    if missing:
        return False, f"no golden recorded for cases {missing}"
    diffs = obs.get("diffs")
    if not isinstance(diffs, dict) or not diffs:
        return False, "probe reported no per-case diffs"
    changed = {case: d for case, d in diffs.items() if d}
    if changed:
        return False, f"composed command drifted: {changed}"
    if obs.get("byte_identical") is not True:
        return False, "probe did not confirm byte identity"
    return True, f"byte-identical for cases {sorted(diffs)}"


def c1(record: dict[str, Any]) -> Verdict:
    """Vertex configuration stripped, and the gateway base URL substituted."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    host = obs.get("host_env") or {}
    if not isinstance(host, dict) or not {"CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION"} <= set(host):
        return False, "the host environment did not set the Vertex vars, so nothing was stripped"
    leaked = obs.get("vertex_vars_present_in_sandbox_env")
    if leaked:
        return False, f"Vertex vars reached the sandbox env: {leaked}"
    sandbox_env = obs.get("sandbox_env") or {}
    if not isinstance(sandbox_env, dict) or not sandbox_env:
        return False, "no sandbox environment was captured"
    if obs.get("anthropic_base_url") != "https://inference.local":
        return False, f"ANTHROPIC_BASE_URL is {obs.get('anthropic_base_url')!r}"
    # A forwarded credential is the same class of defect: host inference configuration crossing a
    # boundary it should not. Checked by key, because composed environments are redacted before
    # being printed and a value-based check would see the mask instead of the secret.
    if not obs.get("host_credential_vars_set"):
        return False, "the host set no credentials, so a leak could not have been observed"
    leaked = obs.get("credential_keys_leaked_into_sandbox_env")
    if leaked:
        return False, f"host credentials crossed into the sandbox env: {leaked}"
    if not obs.get("pinned_placeholder_present"):
        return False, "ANTHROPIC_API_KEY is not the pinned placeholder"
    return True, f"Vertex vars absent, no credential key crossed, base URL {obs.get('anthropic_base_url')}"


def c2(record: dict[str, Any]) -> Verdict:
    """The base URL is exactly the gateway root — Claude Code appends /v1/messages itself."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    url = obs.get("anthropic_base_url")
    if url != "https://inference.local":
        return False, f"expected exactly https://inference.local, got {url!r}"
    if obs.get("has_v1_suffix") or obs.get("has_trailing_slash"):
        return False, f"base URL carries a suffix: {url!r}"
    return True, f"base URL is exactly {url}"


def c22(record: dict[str, Any]) -> Verdict:
    """`--tmux-persist` refused while parsing, not after work has been done."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("exit_code") != 2:
        return False, f"expected an argparse exit code of 2, got {obs.get('exit_code')}"
    if not obs.get("mentions_tmux"):
        return False, "the error does not name tmux"
    markers = obs.get("runtime_markers_found") or []
    if markers:
        return False, f"failure happened after work was done: {markers}"
    return True, "parse-time refusal naming tmux, with nothing provisioned"


def c23(record: dict[str, Any]) -> Verdict:
    """A bare `--division` is a parse error, and `--target` never enables a division."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("bare_division_exit_code") != 2:
        return False, f"bare --division exited {obs.get('bare_division_exit_code')}, expected 2"
    if not obs.get("argparse_expected_one_argument"):
        return False, "the error is not argparse's missing-argument error"
    if obs.get("control_configured_a_division"):
        return False, (
            "--target local with no --division still configured one, which is inheritance: "
            f"{obs.get('control_division_config')}"
        )
    return True, "bare --division is a parse error; --target does not imply a division"


def c24(record: dict[str, Any]) -> Verdict:
    """Missing growth context warns on stderr, names both variables, and exits 0."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("exit_code") != 0:
        return False, f"exit code was {obs.get('exit_code')}; C24 requires 0 — warn, never fail"
    named = obs.get("warning_names_each_var") or {}
    if not isinstance(named, dict) or not named or not all(named.values()):
        return False, f"the warning does not name every missing variable: {named}"
    if obs.get("control_with_vars_set_warned"):
        return False, "the warning also fires when both variables are set, so it is unconditional"
    return True, "warned on stderr naming both variables, exit code 0"


def c25(record: dict[str, Any]) -> Verdict:
    """Growth context that is present on the host reaches the sandbox."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    host = obs.get("growth_vars_set_on_host") or {}
    if not isinstance(host, dict) or not host:
        return False, "the host had no growth context set, so nothing could be forwarded"
    missing = obs.get("missing_from_sandbox_env") or []
    if missing:
        return False, f"growth context did not reach the sandbox: {missing}"
    forwarded = obs.get("growth_vars_in_sandbox_env") or {}
    if forwarded != host:
        return False, f"forwarded values differ from the host's: {forwarded} vs {host}"
    return True, f"both growth variables forwarded intact: {sorted(host)}"


def c9(record: dict[str, Any]) -> Verdict:
    """The driver-config key matches the gateway's compute driver, for both backends."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("podman_gateway_top_level_keys") != ["podman"]:
        return False, f"podman gateway rendered {obs.get('podman_gateway_top_level_keys')}"
    if obs.get("docker_gateway_top_level_keys") != ["docker"]:
        return False, f"docker gateway rendered {obs.get('docker_gateway_top_level_keys')}"
    return True, "driver-config key matches the driver for both backends"


def c10(record: dict[str, Any]) -> Verdict:
    """An unconfirmed `enable_bind_mounts` refuses to provision, with an actionable message."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("exit_code") == 0:
        return False, "exited 0 — it did not fail fast"
    if not obs.get("names_enable_bind_mounts"):
        return False, "the message does not name enable_bind_mounts, so it is not actionable"
    if obs.get("provisioned_anything"):
        return False, "something was provisioned before failing"
    return True, "refused to provision, naming enable_bind_mounts"


def c11(record: dict[str, Any]) -> Verdict:
    """The build pod never asks for privilege."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    for key, label in (
        ("contains_privileged_true", "privileged: true"),
        ("contains_sys_admin", "SYS_ADMIN"),
        ("contains_host_path", "hostPath"),
    ):
        if obs.get(key):
            return False, f"rendered manifest contains {label}"
    findings = obs.get("audit_findings")
    if findings:
        return False, f"audit reported: {findings}"
    caps = (obs.get("security_context") or {}).get("capabilities") or {}
    if sorted(caps.get("add") or []) != ["SETGID", "SETUID"]:
        return False, f"capabilities added are {caps.get('add')}, expected exactly SETUID+SETGID"
    return True, "unprivileged: no privileged/SYS_ADMIN/hostPath, capabilities limited to SETUID+SETGID"


def c12(record: dict[str, Any]) -> Verdict:
    """Rootless buildah's three prerequisites are present."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if obs.get("buildah_isolation") != "chroot":
        return False, f"BUILDAH_ISOLATION is {obs.get('buildah_isolation')!r}, not chroot"
    if not obs.get("container_storage_mount_present"):
        return False, "nothing is mounted at the container-storage path"
    if obs.get("container_storage_volume_kind") != ["emptyDir"]:
        return False, f"container storage is {obs.get('container_storage_volume_kind')}, not emptyDir"
    if not (obs.get("subuid_mounted") and obs.get("subgid_mounted")):
        return False, "subuid/subgid ranges are not present"
    return True, "chroot isolation, emptyDir container storage, subuid/subgid present"


def c13(record: dict[str, Any]) -> Verdict:
    """`--pod-patch` changes only the patched field, and the guarantees still hold."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if not obs.get("patched_resources"):
        return False, "the patched field did not appear in the output"
    unchanged = obs.get("other_container_fields_unchanged") or {}
    changed = [k for k, v in unchanged.items() if not v]
    if changed:
        return False, f"the patch also changed {changed}"
    if not obs.get("container_count_unchanged"):
        return False, "the container list was replaced rather than merged"
    if obs.get("audit_findings_after_patch"):
        return False, f"C11 no longer holds after the patch: {obs.get('audit_findings_after_patch')}"
    if obs.get("buildah_isolation_after_patch") != "chroot":
        return False, "C12 no longer holds after the patch"
    return True, "only the patched field changed; C11 and C12 still hold"


def c14(record: dict[str, Any]) -> Verdict:
    """`--pod-manifest` is used verbatim, and a privileged override warns rather than passing silently."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    if not obs.get("used_verbatim"):
        return False, "the supplied manifest was not used verbatim"
    if not obs.get("audit_findings"):
        return False, "the privileged override produced no audit finding"
    if not obs.get("warned_on_stderr"):
        return False, "the privileged override was accepted without a warning"
    return True, "supplied manifest used verbatim, with a warning about the privilege it requests"


def c18(record: dict[str, Any]) -> Verdict:
    """The MCP allowlist matches the intended tool set exactly."""
    early = _require_ok(record)
    if early:
        return early
    obs = _observations(record)
    tools = obs.get("tool_names")
    if not tools:
        return False, "no tool rules were rendered"
    if obs.get("extra_tools"):
        return False, f"extra tools in the allowlist: {obs.get('extra_tools')}"
    if obs.get("missing_tools"):
        return False, f"tools missing from the allowlist: {obs.get('missing_tools')}"
    if obs.get("wildcard_rules"):
        return False, f"wildcard rule present: {obs.get('wildcard_rules')}"
    if obs.get("rules_without_a_tool_name"):
        return False, "a rule matches a method without naming a tool, which constrains nothing"
    if obs.get("rules_not_allow_wrapped"):
        # The gateway rejects the flat form at parse time, so a policy in that shape is not a
        # narrower boundary — it is no sandbox at all.
        return False, f"rules are not allow-wrapped: {obs.get('rules_not_allow_wrapped')}"
    return True, f"allowlist matches exactly: {tools}"


ORACLE: dict[str, Callable[[dict[str, Any]], Verdict]] = {
    "C1": c1,
    "C9": c9,
    "C10": c10,
    "C11": c11,
    "C12": c12,
    "C13": c13,
    "C14": c14,
    "C18": c18,
    "C2": c2,
    "C3": c3,
    "C4": c4,
    "C21": c21,
    "C22": c22,
    "C23": c23,
    "C24": c24,
    "C25": c25,
}


def judge(criterion_id: str, record: dict[str, Any]) -> Verdict:
    """Apply the oracle for one criterion. Unknown criteria are unproven, never passing."""
    fn = ORACLE.get(criterion_id)
    if fn is None:
        return False, f"no oracle implemented for {criterion_id}"
    return fn(record)
