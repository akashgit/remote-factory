# OpenShell Target: Sandboxing the Agent Runtime for Untrusted Input

*Design proposal — September 2026*

## 1. The problem

`factory contained` runs the factory somewhere other than your shell, and it is honest about
what it is: a reproducibility tool, not a security boundary. The docs say so plainly, and the
two existing targets (local podman, cluster pod) both fit that description — the agent's code
runs with normal network access and your inference credentials sit inside the runtime.

That stance is correct for everyday use, but it forecloses a class of runs we increasingly
want: **modes whose input we do not trust.** A factory run driven by an arbitrary GitHub issue,
a spec scraped from an untrusted source, or a codebase a stranger sent us will execute
LLM-authored shell commands, file writes, and network calls. Today the only way to run those
is on a machine we are willing to lose.

The proposal: a third `--target openshell`, which runs the factory inside an
[OpenShell](https://github.com/NVIDIA/OpenShell) sandbox — a policy-governed runtime where the
allowlist is enforced by the kernel (Landlock for filesystem, seccomp for syscalls, a
supervisor proxy for all network egress), not by the agent's own good behavior. The agent
literally has no skip-permissions flag to abuse; a tool outside the allowlist fails at the
syscall, not at a prompt it can talk its way past.

## 2. What OpenShell is

OpenShell (NVIDIA, Apache-2.0) is a Rust system — a gateway control plane, a trusted
supervisor, and an untrusted sandbox side that runs the agent — with a thin Python SDK
(`openshell` on PyPI) talking gRPC to the gateway. The pieces that matter to us:

- **Deny-by-default network.** The sandbox has no network device. Every connection attempt is
  intercepted (seccomp user-notify) and handed to the supervisor, which decides against policy
  and opens approved connections itself. Rules are per-binary and per-endpoint.
- **Filesystem allowlists.** Landlock restricts reads and writes to declared paths.
- **Credentials that never enter the sandbox.** Providers hold secrets in the gateway; the
  agent sees an opaque placeholder that the supervisor resolves only for requests to the
  endpoints the provider allows — so a key bound to `api.anthropic.com` cannot be exfiltrated
  to anywhere else. There is a first-class `claude-code` provider profile upstream.
- **A policy prover.** Policy changes are formally verified before approval.

Its canonical example is exactly our use case: running Claude Code as the sandbox's main
process behind a policy.

## 3. Design decisions

These four decisions were made during the design review with Akash; alternatives considered
are noted with each.

**SDK + CLI hybrid.** The Python SDK handles lifecycle (create, exec, list, delete), but has no
file upload/download and no interactive terminal — only one-shot `exec()`. Those two gaps are
filled by shelling out to the `openshell` CLI, which is required on the host anyway for gateway
setup. *Alternative: CLI-only* would fit our compose-don't-execute pattern more cleanly but
gives up typed spec/policy construction; *SDK-only* would mean reimplementing tar-over-gRPC
sync badly.

**Credentials via provider, not environment.** The sandbox attaches a `claude-code` provider at
create time; the real API key stays in the gateway and the sandbox holds a placeholder. This is
the single biggest security delta from the local target, where credentials live inside the
container. There is deliberately **no env-var fallback** — a run without a configured provider
fails fast with the `provider create` command as the fix. A fallback would make the insecure
path the path of least resistance.

**Optional dependency.** The SDK drags in grpcio, protobuf, cloudpickle, and httpx. It ships as
a `contained-openshell` extra; the target raises a fix-carrying error when the extra is absent,
and `verify` reports it as a prerequisite.

**Unattended runs, like k8s — and in tmux, like k8s.** An earlier draft ran the factory command
as the sandbox's canonical main process. That does not survive contact with the provenance
contract: the five assertions must run **after** the workspace is in place and **before** the
first agent call, and the supervisor launches the main process as soon as the sandbox is ready —
there is no window between. So the structure is exactly the k8s target's: the main process is an
idle command (`sleep infinity`, which must outlive the run so a failed one stays inspectable),
and the run itself starts in a detached tmux session after the probes pass — `build_tmux_launch`
is shared with the other two targets. Attach goes through the CLI's interactive `exec --tty`.

**The default policy is a Python builder.** It is testable (snapshot tests against the
rendered policy) and versioned with the factory. YAML is the override format only — the
format a `--policy` file is written in, not the format the default ships in.

## 4. Execution model

The run path mirrors `run_k8s`, because both targets share the same shape: nothing
bind-mounted from the host, workspace uploaded as a copy, run unattended.

1. Resolve the project from the verbatim payload, validate `--env`/`--forward` — before
   anything is created.
2. Materialize a self-contained workspace copy (carrying its own `.git`, like k8s).
3. Build the plan: image (the existing `factory-runtime` image — no new image, no CI changes),
   env (configuration only — never credentials), provider, policy, and the launch command.
   The launch command reuses `build_run_command()`, which both existing targets share for
   claude-state seeding.
4. Create the sandbox via the SDK (`from_active_cluster()`, or `--gateway NAME`).
5. Upload the workspace via the CLI.
6. Run the **provenance probes** via `SandboxSession.exec()` — the probes are already
   transport-agnostic argv lists (that was the point of their design), so this target gets the
   five assertions between provisioning and the first agent call for free. A failed assertion
   aborts naming the file and likely cause, leaving the sandbox up for inspection.
7. Start the run in a detached tmux session (shared `build_tmux_launch`); record the target;
   print the attach/sync/rm commands.

`FACTORY_CONTAINED_DRY_RUN=1` prints the plan — SDK operations as described steps, CLI calls as
exact argv. The SDK calls are not argv, so the plan carries both forms; this is the one place
the compose-don't-execute invariant needs restating rather than inheriting.

## 5. The default policy

This is why the target exists, so the default is strict and the escape hatch is explicit:

- **Filesystem:** read-write `/sandbox` (the workspace, including `.factory/` state);
  read-only elsewhere for the toolchain. Nothing writable outside the workspace.
- **Process:** non-root (the runtime image's arbitrary-UID convention already covers this).
- **Network:** deny-by-default, with a deliberately small default allowlist: the `claude`
  binary may reach `api.anthropic.com`, `statsig.anthropic.com`, and `sentry.io` (mirroring
  OpenShell's own claude-code provider profile), and `pip`/`uv` may reach `pypi.org` and
  `files.pythonhosted.org` so eval environments can be built inside the sandbox. The defaults
  start slightly loose on purpose: loosening later as we learn how the target is used is a
  compatible change, while silently tightening defaults would break running workflows. The
  allowlist stays small enough that every entry is defensible in a review.
- A new openshell-only `--policy <path>` flag accepts a full OpenShell policy. **It replaces
  the default policy entirely — it never merges with it.** Three reasons:
  1. Additive merge needs negations/removals to express "everything except X", which means
     writing and maintaining merge semantics we do not otherwise need.
  2. WYSIWYG: the file a user passes is the whole policy, so nothing hidden can creep in
     through defaults interacting with overrides.
  3. OpenShell already layers configuration (provider rules and baseline paths on top of the
     base policy); adding a factory-side merge would be a fourth layer for a reader to
     reconstruct.

MCP tool-level allowlisting (OpenShell's L7 `mcp` network rules) is a natural follow-up and is
deliberately out of scope for v1.

## 6. Wiring

The target interface is convention-based rather than a protocol, so the changes are a fixed
list of dispatch sites:

| Site | Change |
|---|---|
| `factory/cli/contained.py` | `--target` choices + dispatch to the new `run_openshell` |
| `factory/cli/contained_args.py` | openshell-scoped flags (`--policy`, `--gateway`), scoping enforcement, help text |
| `factory/cli/contained_openshell.py` | new — the run path, peer of `contained_local`/`contained_k8s` |
| `factory/contained/openshell.py` | new — all OpenShell knowledge: guarded SDK import, plan model, policy builder, CLI argv composers, run listing |
| `factory/contained/usage.py` | add `"openshell"` to `TARGETS` (without this, `ls`/`record_target` silently ignore the target) |
| `factory/contained/lifecycle.py` | listing union + attach/rm/sync branches |
| `factory/contained/setup.py`, `prereq.py` | setup steps and verify checks |

Sandbox discovery for `ls` works the same way as podman's: `SandboxSpec.labels` is a
`map<string, string>` and the list RPCs accept a `label_selector`, so the factory stamps
`factory.contained=true` (plus `factory.project` / `factory.name`, mirroring the podman
labels) and lists on the selector — no name-prefix matching. The sandbox's name is the run_id.
Other factory-family tools (e.g. refactory-lightwell) can add their own labels to sandboxes
they manage without colliding with ours.

## 7. What this does and does not protect

Worth being precise about, since the existing contained docs promise *not* to be a security
sandbox and this target changes that sentence's scope:

**Does:** confine agent-authored code at the kernel level — filesystem writes outside the
workspace, egress outside the allowlist, and credential exfiltration to non-approved endpoints
all fail closed, regardless of what the agent CLI's own permission flags say.

**Does not:** make the gateway host untrusted (the gateway and supervisor are part of the
trusted computing base); provide multi-tenant isolation between runs; or make the run's diff
safe to merge — review still applies.

## 8. Setup and verification

`setup` gains an openshell half: CLI binary, SDK import (via the extra), gateway reachable,
compute driver available (read from the gateway — we do not probe podman ourselves), runtime
image, and `claude-code` provider configured. `verify` streams the same checks with a fix for
each failure, per the existing patterns.

## 9. Testing

The discipline from the k8s tests carries over: never reach a real gateway (patch client
construction the way `test_contained_k8s.py` stubs `list_contexts`), test CLI composers as
pure functions, a dry-run fidelity test (prints the plan, provisions nothing, redacts
secrets), a policy-builder snapshot test, and an env-composition test asserting no credential
key crosses the boundary.

## 10. Open questions

Three questions from the first review round are now settled (Rohan):

- **Default egress:** the default allowlist includes the read-only package registries, not
  inference endpoints only. Defaults start slightly loose and are tightened based on observed
  usage — loosening is compatible, silent tightening is not.
- **`--policy` is full-replace, not merge.** Merge semantics would need negations we do not
  want to maintain, WYSIWYG beats hidden defaults, and OpenShell already layers provider rules
  on the base policy — a factory-side merge would be a fourth layer.
- **Discovery is label-based:** `factory.contained=true` on the sandbox, run_id as the name.
  (An earlier draft assumed OpenShell had no label support; it does — `SandboxSpec.labels` and
  `label_selector` on the list RPCs.)

Remaining:

1. **SDK version floor.** Needs confirmation against the minimum `openshell` release with
   `from_active_cluster()` and `SandboxSpec.providers`.
2. **Default allowlist contents over time.** The PyPI rule is the first entry that exists for
   usability rather than necessity. How do we review additions — treat the default policy like
   code (PR review of the builder) and leave it at that?

## 11. Implementation order

1. Optional dependency + `factory/contained/openshell.py` skeleton
2. Policy builder + plan composition (dry-run ready)
3. Run path + dispatch wiring
4. Lifecycle (ls/attach/rm/sync)
5. Setup/verify checks
6. Tests
7. Docs (`docs/contained/index.md` target table + security-story rewrite, `CLAUDE.md`)

---

*Working notes for implementation live in `.claude/plans/openshell-contained-target.md` (not
committed). This document is the shareable version.*
