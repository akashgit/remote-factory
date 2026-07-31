#!/usr/bin/env python3
# COVERS: C1,C2,C25
"""C1/C2/C25 — the environment `factory contained` composes for the sandbox.

Forwarding the host's Vertex configuration into a sandbox makes Claude Code dial Vertex directly,
which the sandbox's deny-by-default egress policy refuses. The resulting error looks like a network
policy fault, so it is expensive to diagnose and easy to misattribute — which is why this is checked
mechanically rather than trusted.

The probe deliberately sets `CLAUDE_CODE_USE_VERTEX` and `CLOUD_ML_REGION` in the host environment
first. Without that, "the sandbox env contains neither" would be vacuously true and would prove
nothing; the host environment is reported alongside the result so a judge can confirm the setup
actually created something to strip.
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

# Present in the host environment, and each must be absent from the composed sandbox environment.
HOST_VERTEX_ENV = {
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLOUD_ML_REGION": "us-east5",
    "ANTHROPIC_VERTEX_PROJECT_ID": "itpc-gcp-ai-eng-claude",
}

# Credential-bearing variables that must not cross the boundary at all. Credentials belong on the
# gateway (spec §8); one that crosses lands in the sandbox argv and in this evidence file.
#
# The check is on the *key*, not the value: composed environments are redacted before being printed,
# precisely so a leak does not get written into a retained evidence file. A value-based check would
# therefore see the mask rather than the secret and would report a leak as clean.
HOST_CREDENTIAL_ENV = {
    "ANTHROPIC_API_KEY": "sk-ant-probe-host-credential",
    "OPENAI_API_KEY": "sk-openai-probe-host-credential",
    "CODEX_API_KEY": "codex-probe-host-credential",
    "BOBSHELL_API_KEY": "bob-probe-host-credential",
}

# The only credential key legitimately present in the sandbox, and only with the pinned placeholder.
PINNED_PLACEHOLDER = ("ANTHROPIC_API_KEY", "unused")

# Growth-dimension context. Present here, so C25 checks it survives rather than checking absence.
HOST_GROWTH_ENV = {
    "FACTORY_MANAGED_DIRS": "/tmp/factory-eval-contained/managed",
    "FACTORY_VAULT_PATH": "/tmp/factory-eval-contained/vault",
}

BASE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp/factory-eval-contained/home",
    "LANG": "C",
    "FACTORY_OPENSHELL_DRY_RUN": "1",
    **HOST_VERTEX_ENV,
    **HOST_CREDENTIAL_ENV,
    **HOST_GROWTH_ENV,
}

EXPECTED_BASE_URL = "https://inference.local"


def main() -> int:
    if not factory_bin():
        note("t1_contained_env: no factory entry point")
        return 1

    project = fresh_dir("env_project")
    fresh_dir("home")
    (project / ".gitignore").write_text(".factory/\n")
    factory_state = project / ".factory"
    factory_state.mkdir()
    (factory_state / "config.json").write_text('{"name": "env_project"}\n')

    cmd = factory_bin() + ["contained", str(project), "--mode", "improve"]
    capture = run(cmd, env=dict(BASE_ENV), cwd=project, timeout=120.0)

    payload: dict[str, object] = {}
    parse_error = ""
    try:
        payload = json.loads(str(capture.get("stdout", "")))
    except json.JSONDecodeError as exc:
        parse_error = f"dry-run output was not JSON: {exc}"

    sandbox_env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    assert isinstance(sandbox_env, dict)
    base_url = sandbox_env.get("ANTHROPIC_BASE_URL")

    # A credential key counts as leaked when it appears in the sandbox env at all — except the
    # pinned placeholder, whose presence is the thing C1 wants to see.
    placeholder_key, placeholder_value = PINNED_PLACEHOLDER
    leaked_credentials = sorted(
        key
        for key in HOST_CREDENTIAL_ENV
        if key in sandbox_env
        and not (key == placeholder_key and sandbox_env.get(key) == placeholder_value)
    )

    shared = {
        "host_env": dict(sorted(BASE_ENV.items())),
        "host_vertex_vars_set": sorted(HOST_VERTEX_ENV),
        "host_credential_vars_set": sorted(HOST_CREDENTIAL_ENV),
        "sandbox_env": dict(sorted(sandbox_env.items())),
        "parse_error": parse_error,
    }

    emit(
        probe_record(
            "C1",
            "t1",
            observations={
                **shared,
                "vertex_vars_present_in_sandbox_env": sorted(
                    k
                    for k in ("CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID")
                    if k in sandbox_env
                ),
                "credential_keys_leaked_into_sandbox_env": leaked_credentials,
                "pinned_placeholder_present": sandbox_env.get(placeholder_key) == placeholder_value,
                "anthropic_base_url": base_url,
            },
            invocations=[capture],
        )
    )
    emit(
        probe_record(
            "C2",
            "t1",
            observations={
                **shared,
                "anthropic_base_url": base_url,
                "expected_exactly": EXPECTED_BASE_URL,
                "has_v1_suffix": bool(base_url) and str(base_url).rstrip("/").endswith("/v1"),
                "has_trailing_slash": bool(base_url) and str(base_url).endswith("/"),
            },
            invocations=[capture],
        )
    )
    emit(
        probe_record(
            "C25",
            "t1",
            observations={
                **shared,
                "growth_vars_set_on_host": dict(sorted(HOST_GROWTH_ENV.items())),
                "growth_vars_in_sandbox_env": {
                    k: sandbox_env.get(k) for k in sorted(HOST_GROWTH_ENV) if k in sandbox_env
                },
                "missing_from_sandbox_env": sorted(
                    k for k in HOST_GROWTH_ENV if k not in sandbox_env
                ),
            },
            invocations=[capture],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
