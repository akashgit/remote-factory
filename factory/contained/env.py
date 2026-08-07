"""Which environment variables cross into a contained run, and what is masked when one is printed.

Credential material genuinely crosses into the runtime: nothing outside it terminates inference on
its behalf. `CLAUDE_CODE_*` and `CLOUD_ML_*` have to cross for the Vertex path to work, and
`ANTHROPIC_API_KEY` for the direct one.

So the policy is: `FACTORY_` by default, plus exactly what `--forward` names, plus the backend
variables the resolved credential shape requires (`factory.contained.credentials`). Nothing
implicit — a variable that is not in one of those three sets does not cross, and the three sets are
each visible at the call site rather than accumulated by prefix matching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Set in the environment the factory runs with inside the container. Everything that has to behave
# differently in there reads it through `in_contained()` rather than checking the variable, so
# there is one answer to "am I contained?" and one place to change it. The `FACTORY_` prefix is
# deliberate: the forwarding policy below carries it inward without a special case.
CONTAINED_ENV_VAR = "FACTORY_CONTAINED"


def in_contained(env: dict[str, str] | None = None) -> bool:
    """True when this process is the factory running inside a `factory contained` runtime."""
    source = os.environ if env is None else env
    return source.get(CONTAINED_ENV_VAR, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class EnvPolicy:
    """Which environment variables cross into a wrapped invocation, and what replaces them.

    `forward_prefixes` selects variables from the caller's environment by prefix. `drop_prefixes`
    and `drop_keys` then remove variables a prefix swept in but that must not cross — the prefixes
    are coarse, and a policy needs a way to say "everything under FACTORY_, except this".
    `substitutions` are applied last and always win, so a policy can both refuse to forward a
    variable and pin a different value for it.
    """

    forward_prefixes: tuple[str, ...]
    drop_prefixes: tuple[str, ...] = field(default=())
    drop_keys: tuple[str, ...] = field(default=())
    substitutions: tuple[tuple[str, str], ...] = field(default=())

    def resolve(self, environ: dict[str, str]) -> dict[str, str]:
        """Return the environment the wrapped invocation should see, sorted by key."""
        forwarded = {
            k: v
            for k, v in environ.items()
            if k.startswith(self.forward_prefixes)
            and not (self.drop_prefixes and k.startswith(self.drop_prefixes))
            and k not in self.drop_keys
        }
        forwarded.update(dict(self.substitutions))
        return dict(sorted(forwarded.items()))


# Host-only `FACTORY_` controls. Each one describes *this* invocation or a *host* path, so
# forwarding it either puts the contained factory into a mode it was never asked for or points it
# at a directory that does not exist inside.
_HOST_ONLY_FACTORY_KEYS = (
    "FACTORY_CONTAINED_DRY_RUN",   # a decision about this invocation, not the contained one
    "FACTORY_CONTAINED_HOME",      # a host path; inside, the workspace is already the workspace
    "FACTORY_CONTAINED_IMAGE",     # already resolved into the plan by the time this is composed
)

CONTAINED_ENV_POLICY = EnvPolicy(
    forward_prefixes=("FACTORY_",),
    drop_prefixes=("FACTORY_EVAL_",),
    drop_keys=_HOST_ONLY_FACTORY_KEYS,
    substitutions=((CONTAINED_ENV_VAR, "1"),),
)

# Values under these key fragments are masked wherever a composed environment is printed or logged.
# Substituted values are never masked — they are placeholders whose presence is the thing being
# verified, and hiding them would defeat the check.
_SECRET_KEY_FRAGMENTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_REDACTED = "<redacted>"


def is_secret_key(key: str) -> bool:
    return any(fragment in key.upper() for fragment in _SECRET_KEY_FRAGMENTS)


def redact_env(env: dict[str, str], policy: EnvPolicy) -> dict[str, str]:
    """Mask secret-looking forwarded values so they cannot reach logs or evidence files."""
    pinned = dict(policy.substitutions)
    return {
        key: (value if key in pinned or not is_secret_key(key) else _REDACTED)
        for key, value in env.items()
    }


def redact_argv(argv: list[str], policy: EnvPolicy) -> list[str]:
    """Mask secret-looking `--env KEY=VALUE` pairs in a composed command line.

    Credentials now genuinely cross the boundary, so this is no longer a belt-and-braces
    check against a policy that already refused to forward them — it is the only thing standing
    between a real API key and every dry-run transcript, log line and evidence file.
    """
    pinned = dict(policy.substitutions)
    out: list[str] = []
    for index, token in enumerate(argv):
        previous = argv[index - 1] if index else ""
        if previous == "--env" and "=" in token:
            key, _, value = token.partition("=")
            out.append(f"{key}={_REDACTED}" if key not in pinned and is_secret_key(key) else token)
            continue
        out.append(token)
    return out
