"""Sandbox mode detection — is this factory process running inside an OpenShell sandbox?

`factory contained` sets ``FACTORY_SANDBOX=1`` in the environment it composes for the in-sandbox
invocation. Everything that has to behave differently inside a sandbox reads it from here rather
than checking the variable directly, so there is one answer to "am I contained?" and one place to
change it.

The variable name starts with ``FACTORY_`` deliberately: the existing env-forwarding prefixes carry
it into the sandbox without a special case.
"""

from __future__ import annotations

import os

SANDBOX_ENV_VAR = "FACTORY_SANDBOX"

# Claude Code inside a sandbox talks to the OpenShell gateway's inference proxy in Anthropic-API
# mode. No `/v1` suffix — Claude Code appends `/v1/messages` itself, and a doubled path fails in a
# way that looks like a network policy problem rather than a configuration one.
SANDBOX_INFERENCE_BASE_URL = "https://inference.local"

# The proxy strips this and substitutes the real credential; it never reaches upstream. Claude Code
# only needs *something* here to skip its OAuth login flow.
SANDBOX_INFERENCE_API_KEY = "unused"


def in_sandbox(env: dict[str, str] | None = None) -> bool:
    """True when this process is running inside an OpenShell sandbox."""
    source = os.environ if env is None else env
    return source.get(SANDBOX_ENV_VAR, "").strip().lower() in ("1", "true", "yes")
