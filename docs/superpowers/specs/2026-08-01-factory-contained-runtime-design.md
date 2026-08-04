# `factory contained` — sandboxed and in-cluster runtimes

**Date:** 2026-08-01
**Status:** Design — awaiting review
**Supersedes:** `2026-07-29-factory-openshell-runtime-design.md`
**Decision log:** `2026-07-31-contained-rework-log.md` (items A1–G2)

---

## 0. Implementation rule

**One functionality at a time, verified working end to end before the next one starts.**

This is a delivery constraint, not a preference. The work must land as a chain of small reviewable
PRs, not one 5,000-line drop. A phase is finished when it has been *run against a real project and
produced the expected output* — not when it compiles and not when its unit tests pass. §13 names the
evidence each phase owes, and every scenario section below ends with the exact commands that produce
it.

The verification commands deliberately use short, cheap operations against a small real repository
([`beatsmonster/rta`](https://github.com/beatsmonster/rta) — 178KB, Go, already factory-managed).
The goal is to exercise the plumbing fast enough to iterate on it, not to improve that project.

## 1. Goal

Run the factory somewhere other than the developer's shell, without giving up the two things it
cannot work without: the project's filesystem context, and the ability to build a container image,
run it, read the failure, and iterate.

### 1.1 What each runtime is for

**`--target local`** is the everyday runtime. The factory runs on the developer's machine inside an
OpenShell sandbox, on a copy of the project tree. The point is not to protect the project from the
agent — the agent is authorized to edit it. The point is that agent-authored code executes under a
seccomp filter with deny-by-default egress, so a build script that reaches for the network, or a
test that tries to read `~/.ssh`, is stopped by the runtime rather than discovered afterwards. It is
also where interactive work happens: attach, watch a cycle, detach, come back.

**`--target k8s`** is the remote runtime. The factory runs unattended on hardware the laptop is not:
real CPU, real memory, amd64, and a build plane that produces images the cluster can actually run.
The point here is capacity and durability — a run survives closing the laptop, and its workspace
survives the pod.

### 1.2 The guarantees differ, and the design says so

| | `--target local` | `--target k8s` |
|---|---|---|
| Runtime | NVIDIA OpenShell sandbox | Plain pod on Kubernetes/OpenShift |
| Syscall confinement | seccomp filter, `PR_SET_NO_NEW_PRIVS`, inherited by every child | none beyond the container runtime's default |
| Filesystem | Landlock read-only system dirs; project is a **copy**, host tree untouched | restricted SCC; workspace is a **copy** on a PVC |
| Identity | non-root, UID-matched to the host user | restricted SCC, arbitrary namespace UID |
| Egress | deny-by-default, per-binary allowlist, L7 MCP tool filtering | NetworkPolicy only — no L7, no per-binary rules |
| Credentials | held by the OpenShell gateway; nothing crosses into the sandbox | a Secret in the namespace, mounted into the pod |
| Division | full host podman engine | OpenShift `Build` + validation pods |
| Build isolation | division targets are **outside** the sandbox by necessity | builds run in the platform's build pods |

Read the table as three honest admissions:

1. **The division is a hole by design.** Builds cannot happen inside either boundary, so both
   divisions reach outward. Opt-in and separately named is the mitigation for the decision; the
   technical mitigations are narrower.
2. **K8s is the weaker sandbox.** No seccomp filter, no egress allowlist, no MCP-level tool
   filtering. Its boundary is RBAC and the namespace, which is why the k8s division's agent gets
   MCP tools and no shell (§6.2).
3. **Neither replaces review.** `contained` bounds the blast radius of a run; it does not make the
   diff trustworthy.

### 1.3 Non-goals

Not attempting: matching local's egress control in the cluster; multi-tenant use of a shared
gateway; or making the two runtimes behave identically. They share a command surface, not a threat
model.

## 2. Command surface

### 2.1 Shape

```bash
factory contained [runtime flags] -- <any factory command>
```

Everything after `--` is handed to the factory inside the runtime **verbatim** (B2), except for path
rewriting (§2.5). The runtime is a place to run the factory, not a mode of the factory.

```bash
# Scenario 1 — improve a project in a local sandbox, watch it
factory contained -- ceo ~/code/rta

# ...with the local division, so the agent can build and run images
factory contained --division -- ceo ~/code/rta --focus "container image"

# Scenario 2 — an unattended loop in the cluster
factory contained --target k8s --namespace factory-division \
  -- run ~/code/rta --loop --interval 1800

# Scenario 4 — same, with cluster builds
factory contained --target k8s --division -- ceo ~/code/rta

# Anything else the factory can do
factory contained -- study ~/code/rta
factory contained -- agent researcher --task "..." --project ~/code/rta
```

### 2.1a Provenance: the local tree is always the source

**A contained run always starts from the files on the developer's machine.** Never a fresh clone,
never a remote fetch, never whatever `HEAD` happens to be — the working tree as it is right now,
uncommitted changes included. The whole point of running a sandbox is to exercise code that is not
committed yet, and a runtime that silently tested `HEAD` while the developer edited the tree would
be worse than no runtime at all.

Two obligations follow, and both are load-bearing because they fail *quietly*:

**Every transfer is asserted, not assumed.** After the workspace is in place — mounted (§3.2) or
unpacked (§4.4) — and before the factory starts, the runtime checks that what arrived is what the
factory will read:

| Check | Why |
|---|---|
| the project directory exists at the rewritten path and is non-empty | a `dest`-parent mistake nests the tree one level deep and the factory `cd`s into nothing (ATTEMPTS 15) |
| `.git` is present and `git status` succeeds | without it, state detection reports `no_repo`, the CEO silently drops to build mode, and the eventual error names a flag several steps away from the cause (ATTEMPTS 16) |
| `.factory/config.json` is present **when the host had one** | the `.gitignore` trap drops the whole directory; asserting unconditionally instead blames that trap for a project that was simply never initialized (ATTEMPTS 27) |
| the workspace is writable by the runtime identity | a UID-mismatched bind mount is silently read-only (§3.2) |
| a file's content hash matches the host's | proves the *content* arrived, not merely a path — the one check that catches a stale or partial transfer |

A failed assertion aborts before the first agent call, naming the file and the likely cause. A run
that starts on the wrong files wastes an entire cycle and produces a plausible-looking result.

### 2.2 Runtime flags

Parsed before `--`. Everything after is `argparse.REMAINDER`.

`factory contained --help` prints these as the same three tables, not a flat `argparse` list — the
target-scoping is the information a user needs most, and a flat list hides it.

**Both targets:**

| Flag | Default | Meaning |
|---|---|---|
| `--target local\|k8s` | `local` | Which runtime. |
| `--division` | off | Enable the container-manufacturing plane **for the selected target**. Boolean. There are no permutations: local runtime gets the local division, k8s runtime gets the k8s division (F1). |
| `--name NAME` | derived | Runtime name. Local names are capped at 19 chars server-side; truncate the readable stem, never the hash. |
| `--env KEY=VALUE` | — | Extra environment for the runtime, repeatable. The escape hatch for backend quirks. |
| `--forward VAR` | — | Forward a named host variable, repeatable (e.g. `GH_TOKEN`). |
| `--image REF` | published default | Override the runtime image. Different default per target (§7). |

**Local only:**

| Flag | Default | Meaning |
|---|---|---|
| `--mount PATH` | — | Additional host path bind-mounted into the sandbox, repeatable (B5 opt-ins). |
| `--live` | off | Reserved. Mount the real working tree instead of a copy. Not implemented in phase 1; named here so the copy default is understood as a choice. |

**K8s only:**

| Flag | Default | Meaning |
|---|---|---|
| `--namespace NS` | current context | Never hardcoded. |
| `--storage-class SC` | cluster default | Workspace PVC. |

A flag used against the wrong target fails at parse time naming the target it belongs to — never
silently ignored.

### 2.3 Lifecycle subcommands

Every lifecycle subcommand operates **only on runtimes `factory contained` created**, selected by
the factory's own labels (`factory.contained=true`, `factory.project=<hash>`). Sandboxes and pods
created by other means are never listed, never attached to, and never deleted. A tool that shows a
user resources it did not create invites them to assume it manages those too.

| Command | Behavior |
|---|---|
| `factory contained ls` | Lists factory-created runtimes: name, target, project, age, state. Local reads OpenShell labels; k8s reads pod labels in the namespace. One table, both targets. |
| `factory contained attach <name>` | Local: `openshell sandbox exec --name <n> --tty -- tmux attach`. K8s: `oc exec -it <pod> -- tmux attach`. `Ctrl-b d` detaches without stopping the run. |
| `factory contained rm <name>` | Deletes the sandbox or pod. Prompts if the run is still active. K8s: asks before deleting a PVC with unsynced changes. Local: reports where the workspace copy remains (§3.2). |
| `factory contained sync <name>` | Copies the workspace back to the host now. K8s: `oc cp` from the PVC. Local: reports the copy's path and the merge-back command (§3.2). |
| `factory contained setup` | Interactive first-run setup (§2.6). Asks local / k8s / both, runs that target's steps and its `verify` checks in one pass, and ends with the cluster or gateway actually ready. `--target` skips the question. Idempotent. |
| `factory contained bundle` | Prints the namespace prereq YAML to stdout (§8). Never applies it. |
| `factory contained verify` | Checks prerequisites for the selected target and names what is missing (§3.0, §4.0). |

### 2.4 What the host validates, and what it does not

Validated before anything is provisioned: the runtime CLI exists (`openshell` or `kubectl`/`oc`);
the target is reachable; for local, `enable_bind_mounts` and the image's UID (§3.2); for k8s,
everything `factory contained verify` covers.

**Not** validated: the semantics of anything after `--`. The host cannot know which subcommands a
given factory version supports, and a passthrough that second-guesses its payload breaks every time
the CLI grows. Errors surface from inside the runtime, where the user can attach and read them.

The one exception is mechanical and subcommand-agnostic: path rewriting.

### 2.5 Path translation

The runtime does not share the host's filesystem layout, so a path in the passthrough command may
name something that does not exist inside. Rather than teaching the host every subcommand's
arguments, one generic rule applies:

**Any argument that resolves to an existing host path at or under the project root is rewritten to
its in-runtime equivalent.** Everything else is passed through untouched.

| Target | Host path | In-runtime path |
|---|---|---|
| local | `~/code/rta` | `~/.factory-contained/<run>/rta` — the copy, mounted at its own absolute path, identical inside and out (§3.2) |
| k8s | `~/code/rta` | `/workspace/rta` |

Consequences worth stating:

- Locally the rewrite is *path-preserving*: the copy is bind-mounted at the same absolute path it
  occupies on the host, so a path is valid on both sides simultaneously. This is not cosmetic — the
  local division's `image_build` resolves the Containerfile path **on the host** (§5.3), and it
  would read the original tree if the copy were mounted at the original path.
- A host path **outside** the project root is not rewritten and will not exist in the runtime. The
  command fails inside with a plain "no such file" message. `--mount` is how such a path is made
  available deliberately.
- The rewrite is logged at launch, so a surprising path in an error message is traceable.

### 2.6 `setup` is one interactive flow

`factory contained setup` with no arguments is **interactive**. It asks which target the user is
preparing — local, k8s, or both — then runs that target's setup steps and its `verify` checks in a
single pass. It ends in one of two states, never in between: everything green and a printed
`factory contained -- …` command the user can run immediately, or a numbered list of what is still
missing with the command that fixes each.

Two properties make it safe to run at any time:

- **Idempotent.** Re-running changes nothing that is already correct. It is also the supported way to
  repair a partial setup, so nothing needs to be torn down first.
- **Nothing silent.** Every step announces what it will do before doing it. Steps that touch credentials
  or a cluster ask first (§4.0a).

`--target local|k8s` skips the question, for scripts and for the clean room (§9). `verify` is the
same checks with every mutating step removed, so it is always safe against a live setup.

## 3. Scenario 1 — `--target local`

### 3.0 First-run setup

On a machine that has never run `contained`, the factory must not fail with "gateway unreachable"
and leave the user to reconstruct the setup from a wiki. `factory contained setup` performs it, and
`factory contained verify` checks it without changing anything. Both are idempotent.

The steps, all of which are already probed and recorded in `PLAYBOOK.md` §1:

1. **Container engine.** Confirm a podman machine (or Docker) is running. The gateway needs the
   engine's API socket; without it, sandboxes hang in Provisioning.
2. **Gateway binary.** Install the native `openshell-gateway` for the host architecture. Pinned to a
   known-good version, not `latest`.
3. **Certificates.** `openshell-gateway generate-certs` writes the JWT signing material the Docker
   driver requires.
4. **`gateway.toml`.** Written from a template with four values that are wrong by default and break
   quietly: `bind_address = 0.0.0.0` and `host_gateway_ip` (sandboxes otherwise hang in
   Provisioning — upstream issue #1519), `allow_unauthenticated_users = true` (any configured auth
   otherwise locks the CLI out), `enable_bind_mounts = true` (required by §3.2), and the engine's
   socket path.
5. **Start and register.** Launch the gateway, `openshell gateway add … --local`, `gateway select`.
6. **Inference.** Create the provider and set the model. The factory reports what it found rather
   than guessing credentials.
7. **Sandbox image.** Pull or build the factory sandbox image with `--build-arg SANDBOX_UID=$(id -u)`
   (§7).

`verify` reports each of the seven as present/absent with the exact remediation command. `setup`
performs the ones that are safe to automate and prints the ones that are not (credentials, engine
installation).

**`doctor` is not a substitute.** `openshell doctor check` fails on a podman-only Mac with a Docker
error that says nothing about whether the setup works (ATTEMPTS 1). `verify` must make its own
checks.

### 3.1 Provisioning

`openshell sandbox create` blocks until its command exits (ATTEMPTS 9), so the order is fixed:
create with a trivial bootstrap command, arrange state, then `exec` the real run. Names are capped
at 19 characters server-side (PLAYBOOK §3.1). Tracking is by OpenShell labels, so
`openshell sandbox list/delete/logs` keep working unmodified.

**Provisioning prints the runtime's identifier as its first output**, before any long-running work,
and returns it in `-o json`. Everything else — `attach`, `ls`, `rm`, `sync`, `openshell logs` — keys
off that identifier, and a run whose name the user cannot see is a run they cannot manage.

### 3.2 The workspace is a copy (B4-rev)

The factory never writes the host's working tree. At launch it materializes a copy under
`~/.factory-contained/<run>/` and bind-mounts it **at its own absolute path**, identical inside and
out.

- **Git projects:** `git worktree add` from the current HEAD. Cheap, shares the object store, and
  the run's work is already on a branch when it comes back.
- **Everything else:** `rsync -a` honoring `.gitignore`, plus `.factory/` explicitly.

Why the copy is mounted at its own path rather than the original: `image_build` in the local
division resolves the Containerfile path **on the host** (§5.3). Mounting the copy at the original
project path would make the agent write into the copy while the host read the original — a silent
divergence that produces "file not found" for a file the agent can see. Path-preserving mounting
removes the class of bug entirely, and §2.5's rewrite makes it invisible to the user.

`~/.factory-contained/` is deliberately **not** under `~/.factory/`, which is itself mounted
read-write (§3.3); nesting them would produce overlapping bind mounts.

Results come back by review, not by sync: `factory contained sync` prints the copy's path, its
branch, and the merge command. For a git project that is `git -C <project> merge <branch>` or a PR;
for a non-git project, an rsync command. **Nothing is merged automatically.**

Two prerequisites, both checked by `verify`:

- `enable_bind_mounts = true` in `gateway.toml`. It is **not** reported by any API (ATTEMPTS 13), so
  read the local `gateway.toml`; if the gateway is remote, warn and let the create fail by name
  rather than refusing pre-emptively.
- A sandbox image built with `--build-arg SANDBOX_UID=$(id -u)`. The mount carries host ownership
  through unchanged, so a mismatched identity makes the tree read-only. Provisioning probes
  writability before starting the run.

### 3.3 User-local context (B5)

`~/.factory/` is mounted **read-write**: config, credential profiles, the registry, and ACE-evolved
playbooks work as on the host and keep accumulating.

`~/.claude/projects/`, `FACTORY_MANAGED_DIRS`, `FACTORY_VAULT_PATH` and `GH_TOKEN` are **opt-in** via
`--mount` / `--forward`. When the growth directories are absent, warn loudly at launch: growth
dimensions merge 50/50 into the composite score, so in-sandbox eval scores are not comparable to
host scores. Warn, never fail.

**Interaction with the factory's own worktrees.** `factory/worktree.py:76` creates experiment
worktrees at `<project>/.factory-worktrees/`, inside the project — so inside the *copy*, which is
correct and needs no special handling. Two things must hold anyway, and both belong in the phase-1
tests:

- the copy is a valid git worktree parent (a `git worktree` inside a `git worktree` works, but the
  `.git` file points at the original repository's object store, which the mount must therefore also
  reach — for a worktree copy this means the original `.git` directory is mounted read-only);
- the registry in the mounted `~/.factory/registry.json` records the *copy's* path, so the host's
  registry gains an entry pointing into `~/.factory-contained/`. `rm` cleans it up.

### 3.4 Session, attach, lifetime (B1, B3)

The run starts **detached inside tmux** in the sandbox. `factory contained attach <name>` runs
`openshell sandbox exec --name <name> --tty -- tmux attach`; `Ctrl-b d` detaches and leaves the run
going.

This reverses the previous design's headless-only rule, which was a consequence of how the factory
called `exec` — openshell 0.0.92 has `exec --tty`, `sandbox connect`, and `ssh-config`. `--mode
design` is therefore permitted again.

The sandbox **persists** after the run. `ls`/`rm` manage it; nothing is auto-reaped, because a
failed run is exactly when its state is worth reading.

### 3.5 Inference

Credentials live on the gateway; the sandbox gets `ANTHROPIC_BASE_URL=https://inference.local` and
`ANTHROPIC_API_KEY=unused`. Two further variables are required against the Vertex backend and are
not optional (PLAYBOOK §2.3): `MAX_THINKING_TOKENS=0`, and an explicit `--model`. OpenShell ignores
the image's `ENV`, so everything is passed with `--env` at create time (ATTEMPTS 6). Only `FACTORY_`
is forwarded from the host environment; no credential prefix crosses the boundary.

### 3.6 Verification

```bash
# 1. Prerequisites, on a clean machine (the Lume clean room, §9)
factory contained verify
factory contained setup
factory contained verify
#   expect: seven checks, all present; setup idempotent on the second run

# 2. Plumbing only — no agent call, so failures are unambiguous
git clone https://github.com/beatsmonster/rta ~/code/rta
factory contained -- backlog-list ~/code/rta
#   expect: the sandbox identifier printed first, then rta's backlog items.
#   Proves: create → copy → mount → path rewrite → exec → output relay.

# 2b. Provenance assertions fire on a broken transfer (§2.1a)
#   with .git deliberately excluded: expect an abort naming .git, before any agent call —
#   not a downstream complaint about --focus or build mode.
#   with an uncommitted edit in the host tree: expect the run to see that edit,
#   proving the runtime started from local files rather than HEAD.

# 3. Workspace isolation
factory contained -- study ~/code/rta
#   expect: observations written under ~/.factory-contained/<run>/rta/.factory/strategy/,
#   and `git -C ~/code/rta status` clean — the host tree untouched.

# 4. Attach and detach
factory contained -- ceo ~/code/rta --focus "add a --version flag"
factory contained ls                     # expect: one running entry
factory contained attach <name>          # expect: live TUI, mid-run
#   Ctrl-b d, then attach again — expect the run still going, output continuous.

# 5. Results
factory contained sync <name>
#   expect: the copy's path, its branch name, and a merge command. No automatic merge.
```

## 4. Scenario 2 — `--target k8s`

### 4.0 Prerequisites and `verify`

The cluster half has no equivalent of a local install, but it does have a prerequisite bundle (§8).
`factory contained verify --target k8s` checks, and names precisely what is missing:

1. `kubectl`/`oc` present and a current context;
2. the namespace exists and is accessible;
3. the bundle's objects exist (ServiceAccount, Role, RoleBinding, PVC);
4. every verb the runtime needs, via `SelfSubjectAccessReview`;
5. the credentials Secret exists and carries the expected keys (§4.5);
6. inference is reachable **from inside the cluster** — a short-lived pod that performs one request,
   not a host-side check that proves nothing about the pod's egress;
7. with `--division`: the OpenShift Build API is present, and the ImageStream exists (§6).

### 4.0a `setup --target k8s` leaves the cluster ready

`verify` reports; `setup` fixes. For the cluster that means `setup` does not stop at printing the
bundle — it leaves the namespace able to run factory pods:

1. resolve the namespace (current context unless `--namespace`), and create it only if asked;
2. **print the full manifest it intends to apply** — every object, in full, not a summary;
3. ask for confirmation;
4. apply it with the **user's own** `oc` credentials, never a token the factory holds;
5. re-run `verify` and print the result.

If the user lacks permission to create any object, `setup` degrades to printing the manifest and the
`oc apply` line to hand to whoever owns the namespace. It never partially applies and reports
success.

This refines E3 rather than reversing it. E3's concern was the factory mutating RBAC on its own;
showing every object and asking first keeps that intact while still ending in a working namespace.
The credentials Secret remains outside this flow — `setup` prints the `oc create secret` command and
never handles the material (§4.5).

**Every failed check carries its fix.** `verify` never reports a bare failure: each one names the
exact command that resolves it — `factory contained bundle | oc apply -f -` for a missing object,
the `oc create secret` line for a missing Secret, `oc project` for a missing context. Where the fix
is not a single command (cluster has no OpenShift Build API), it says what that means for the run
rather than leaving the user to infer it. A check that can detect a problem can almost always name
its remedy, and one that cannot should say so explicitly.

### 4.1 No OpenShell in the cluster (A1)

The factory runs in a plain pod. The OpenShell Helm chart ships a non-optional ClusterRole and
ClusterRoleBinding (`tokenreviews`, `nodes`, namespace reads), which A2's namespace-only guardrail
forbids. Confinement is therefore k8s-native: restricted SCC, NetworkPolicy, namespace-scoped RBAC.

### 4.2 Host-side driver (C1)

The factory shells out to `kubectl`/`oc`; no Kubernetes client library is added. This is how every
command proven in PLAYBOOK §5.3 already works, and it supplies `exec -it`, `attach`, `cp` and
`port-forward` for free. The binary is checked at parse time with an actionable message.

### 4.3 Image (C2)

A slim UBI9 image: the factory wheel, the agent CLIs, tmux, and — with `--division` —
`kubernetes-mcp-server`. Deliberately **no `oc`** inside (E2b). Built for **amd64** to match cluster
nodes; the developer's arm64 laptop is not the target.

This overrides the original "base it on podman's image" intent: podman-in-pod needs privileged or
`nested-container` SCC, which A2 forbids, and PLAYBOOK §5.1 showed it fails on this cluster anyway
because the node denies `/proc/self/uid_map` writes. Revisit only if A2 is relaxed elsewhere.

**When the division's MCP server is missing,** because the image was overridden with `--image` or
built from an older tag, `verify` says so and points at `factory contained setup --target k8s`,
which builds and pushes a conforming image through the same Build path the division uses (§6.1).
The failure is never allowed to surface as an agent that quietly has no tools.

### 4.4 Workspace (C3)

Not `oc cp` of a directory tree — that is one API round trip per file and is painfully slow on a
repository. Instead:

1. the host packs the workspace into a **tarball** (same file selection as §3.2, `.factory/`
   explicit);
2. it streams the tarball into the PVC once;
3. an **initContainer** unpacks it into `/workspace` before the factory container starts.

The PVC is RWO (one pod mounts it) and survives pod restart, eviction and node drain, so a
multi-hour run is recoverable. `factory contained sync` streams a tarball back the same way.

The `.factory/` directory must be included explicitly: the packer copies what it is told, so the
trap §3.2 removed locally returns here in a different shape. Assert on the receiving side that
`.factory/config.json` arrived — but only when the host had one, because a partially initialized
`.factory/` is a legitimate state and blaming the `.gitignore` trap for it produces a misleading
error (ATTEMPTS 27).

### 4.5 Credentials, and the secret scan (C4)

A Secret the **user** pre-creates as part of the prereq bundle. The factory references it by name and
never reads or writes credential material. `verify` checks it exists, carries the expected keys, and
that the pod can reach inference — so a credentials problem fails at launch with a named cause
rather than inside an agent call.

This reverses §9.14 of the previous design for the k8s path. There is no gateway in-cluster to hold
the key; a credential exists in the namespace by design.

**Before any workspace leaves the machine, it is scanned for secrets.** The k8s path copies a
developer's working tree onto cluster storage, and a `.env` or a stray key file goes with it.
[Gitleaks](https://github.com/gitleaks/gitleaks) runs over the packed file list — regex-based, fully
offline, no network calls, which matters for a step whose purpose is preventing exposure.

Findings are listed with file and line, and the user confirms before the upload proceeds. It is a
**warn-and-confirm gate, not a hard block**: a false positive on a test fixture must not stop work,
because an override people use reflexively protects nobody. `--yes` skips the prompt for automation
and is recorded in the run's evidence. Gitleaks is a documented prerequisite; when it is absent,
`verify` says so and the upload warns that it is unscanned rather than silently proceeding.

### 4.6 Session and attach (C5)

tmux holds the session inside the pod; `factory contained attach` becomes
`oc exec -it <pod> -- tmux attach`.

tmux has no network protocol — its client-server link is a Unix socket — so `oc exec -it` is the
transport in every design. The multiplexer is what makes detaching safe: without it, `oc attach` is
the only route to the running process's stdio and `Ctrl-C` sends SIGINT to the factory.

A pod restart loses the session; the workspace survives on the PVC.

### 4.7 Verification

```bash
# 1. Prerequisites — setup leaves the namespace ready to run factory pods
factory contained setup --target k8s --namespace factory-division
#   expect: the full manifest printed, a confirmation prompt, apply with your own
#   credentials, then verify green. Re-running changes nothing.

oc delete rolebinding factory-scc -n factory-division
factory contained verify --target k8s --namespace factory-division
#   expect: that one RoleBinding named as missing, with the command that restores it.

# 2. Plumbing only
factory contained --target k8s -- backlog-list ~/code/rta
#   expect: pod name printed first, then rta's backlog. Proves pack → PVC →
#   initContainer unpack → path rewrite to /workspace/rta → exec → relay.

# 3. Secret scan
echo 'AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE' > ~/code/rta/.env
factory contained --target k8s -- study ~/code/rta
#   expect: gitleaks names .env and its line, and prompts before uploading.

# 4. Durability
factory contained --target k8s -- run ~/code/rta --loop --interval 300
oc delete pod <pod>
#   expect: the workspace survives on the PVC; `sync` still returns the work.

# 5. Attach
factory contained attach <name>   # expect: live TUI via oc exec -it; Ctrl-b d detaches
```

## 5. Scenario 3 — `--target local --division`

The local division gives the sandboxed agent the host's podman engine, so it can build an image, run
it, read the failure and iterate. Builds cannot happen inside the sandbox at all: OpenShell's
seccomp filter blocks `mount` and `CLONE_NEWUSER` for the agent and every child, with
`PR_SET_NO_NEW_PRIVS` set. The division is the deliberate opening.

### 5.1 The factory starts `podman-mcp-server` (D1)

The factory spawns it, logs under `.factory/`, and stops it when the run ends.

Two mechanical details, both already probed:

- The server speaks **Streamable HTTP** (`--port 8430`, endpoint `/mcp`) — it is not a stdio server.
- It nonetheless **exits silently when stdin reaches EOF**, even in HTTP mode (ATTEMPTS 17), which
  is why a naive background spawn leaves nothing listening and writes no error. The factory holds
  stdin open for the process's lifetime.

It must bind all interfaces to be reachable from the sandbox as `host.openshell.internal`, and it
has **no authentication**. For the life of the run, anything that can reach port 8430 can build and
run containers on the host. This reverses §9.15 of the previous design, by decision. The mitigation
is disclosure, not technology: warn loudly at start, confirm shutdown at exit.

### 5.2 Tool surface (D2)

The full `podman-mcp-server` surface is allowed — build, run, logs, stop, remove, inspect, list,
pull, push, network and volume listing. The agent can pull arbitrary images and push to any registry
the host is logged into.

**This is the first iteration's posture, on purpose.** The goal is a working division; tightening
comes once there is something to tighten against. The intended next steps, when that time comes:
drop `image_push` and the network/volume tools, add the token-gated proxy that D1 declined, and
consider a dedicated podman machine so the agent's images never mix with the user's. Recording them
here keeps "we will lock this down" from becoming folklore.

### 5.3 Policy mechanics

Four things are load-bearing and each was found by a failure:

- `--policy` **replaces** the sandbox default; a partial file leaves the sandbox with no filesystem
  policy at all (ATTEMPTS 11). `factory/templates/sandbox-policy.yaml` is the base to extend.
- Rules are `allow:`-wrapped and key the tool as `tool`, not `name` (ATTEMPTS 12).
- The endpoint needs `mcp.allow_all_known_mcp_methods: true`, or the `initialize` handshake itself is
  denied (ATTEMPTS 18). Keep `strict_tool_names: true`.
- A policy with no `binaries:` list matches no process and denies everything (ATTEMPTS 19).

`image_build` uses the containerFile's own directory as its build context and resolves it on the
**host**, which is why §3.2's path-preserving mount is what makes the local division work at all.

### 5.4 The division ships a brief

`.factory/division/README.md` names the tools, the build → run → read → fix loop, and the fact that
this is a capability the run already has. Without it, a Refiner given only the tool registration
scoped 165 lines of new CLI code to wrap them, while its own task text forbade modifying source
(ATTEMPTS 34).

### 5.5 Verification

```bash
# 1. The server comes up and goes down with the run
factory contained --division -- backlog-list ~/code/rta
#   expect: a launch warning naming the unauthenticated endpoint; after exit,
#   nothing listening on 8430.

# 2. The agent knows it has the capability
factory contained --division -- agent builder \
  --task "List the container tools available to you. Do not write code." --project ~/code/rta
#   expect: the podman tools named, from the brief — not a plan to build a CLI wrapper.

# 3. One real build-validate cycle, kept small
factory contained --division -- ceo ~/code/rta \
  --focus "add a Containerfile that builds the binary; verify it runs --help"
#   expect: image built on the host podman, a container run, --help output read back,
#   and `podman images` on the host showing the built tag.

# 4. The allowlist is real
#   from inside the sandbox, `curl http://host.openshell.internal:8430/mcp` → 403.
#   That is the policy working (PLAYBOOK §4.1), not a fault.
```

## 6. Scenario 4 — `--target k8s --division`

OpenShift only (E2). The launch check detects OpenShift by API presence, not by the `oc` binary, and
refuses elsewhere with a named reason.

### 6.1 Builds go through OpenShift `Build` objects (E1)

The platform's build controller holds the privileges OpenShift reserves for building, and every
object stays namespace-scoped. Rootless buildah, kaniko and buildkit all depend on the `uid_map`
write this cluster's nodes deny — probed to the bottom (PLAYBOOK §5.1, ATTEMPTS 21–23), and not a
manifest problem. Output goes to the cluster-internal registry; the validation pod pulls from
`image-registry.openshift-image-registry.svc:5000` and push credentials stay with the build service
account.

### 6.2 The agent reaches the cluster only through MCP (E2b)

`kubernetes-mcp-server` runs inside the pod, registered over stdio. `oc` is not in the image, which
is what makes the tool allowlist a boundary rather than a decoration.

Because there is no OpenShell egress proxy in this runtime, the three obstacles that cost the most
in the previous iteration do not exist here: no OAuth discovery denial, no stdio bridge over an
allowlisted HTTP endpoint, no Host-rewriting proxy (PLAYBOOK §5.4). Configure the in-cluster
credential source explicitly so the server never auto-detects a provider that wants an interactive
login.

### 6.3 Build context via a sidecar (E1b)

The build context reaches the `Build` through a **factory-controlled sidecar container** in the same
pod, sharing the PVC. The sidecar holds `oc` and the ServiceAccount token; the agent's container
holds neither. The agent requests a build; the sidecar reads the workspace off the PVC and starts a
binary-source Build.

This removes the ~700KB ConfigMap ceiling that previously forced a wheel-only context (ATTEMPTS 25)
without reopening the shell path E2b closed. Two constraints follow and must not be relaxed
casually:

- the sidecar is a **separate container**, not a process beside the agent;
- the agent's tool allowlist **excludes `pods/exec`**, or the agent execs into the sidecar and
  recovers the shell.

The interface is a one-tool stdio MCP server the factory ships — `start_build(dockerfile, tag)` —
registered alongside `kubernetes-mcp-server`.

### 6.4 What the agent may create (E4)

Validation pods only: run a pod on an image it built, read its logs, delete it. Everything it
creates carries the run's label and is swept when the run ends. No Deployments, Services,
ConfigMaps, Secrets or RBAC. Multi-pod integration testing is out of scope until a real case
appears.

### 6.5 The same brief applies

`.factory/division/README.md`, written for the k8s tools and the submit → poll → read-logs → fix →
resubmit → validate loop.

### 6.6 Verification

```bash
# 1. Refuses where it cannot work
factory contained --target k8s --division ...   # against vanilla k8s
#   expect: refusal naming the missing Build API, at launch.

# 2. The agent has tools and knows what they are for
factory contained --target k8s --division -- agent builder \
  --task "List the cluster tools available to you. Do not write code." --project ~/code/rta
#   expect: mcp__kubernetes__* tools plus start_build; no OAuth/needs-auth state.

# 3. One real build-validate cycle
factory contained --target k8s --division -- ceo ~/code/rta \
  --focus "build a container image of this project and verify it runs --help"
#   expect: a Build submitted via the sidecar, pushed to the internal registry,
#   a validation pod the agent launched printing the binary's help output.

# 4. The boundary holds
#   expect: `pods/exec` denied by RBAC; `factory contained verify` asserts its absence.

# 5. Cleanup
#   expect: after the run, no labelled pods remain; the ImageStream retains its tags.
```

## 7. Images

Three, deliberately.

| Image | Contents | Notes |
|---|---|---|
| OpenShell sandbox | factory under `/usr/local/factory`, agent CLIs, tmux | Built `--build-arg SANDBOX_UID`. `/opt` is inaccessible under the default policy (ATTEMPTS 5); the image's `ENV` is ignored (ATTEMPTS 6). No shadow-utils, so a UID change is a `sed` on `/etc/passwd` (ATTEMPTS 20). |
| k8s runtime | UBI9, factory wheel, agent CLIs, tmux, `kubernetes-mcp-server` | amd64. **No `oc`.** |
| Build sidecar | `oc`, the ServiceAccount token | Separate container; the only holder of a shell path to the cluster. |

All built and published by CI. On-demand building stays rejected: it is circular for the sandbox and
slow for every cold start.

## 8. Prereq bundle and RBAC (E3, A2)

`factory contained bundle` emits **plain YAML**; the user applies it with `oc`; `factory contained
verify` checks each object and each required verb via `SelfSubjectAccessReview`, naming what is
missing.

Everything is namespace-scoped (A2). RoleBindings to pre-existing cluster SCCs are allowed; creating
an SCC or ClusterRole is not.

| Object | Purpose |
|---|---|
| ServiceAccount `factory` | the pod's identity |
| Role + RoleBinding | `pods: create/get/list/delete`, `pods/log: get`; with the division, `builds`/`buildconfigs`/`imagestreams`. **Not** `pods/exec`. |
| PVC | the workspace (§4.4) |
| Secret | inference credentials, user-created (§4.5) |

Per-cluster variation — namespace, storage class, image reference — is a flag on the generator, not
a template value.

## 9. Development environment (G1)

The riskiest code in this design is §3.0, the first-run setup, and it can only be tested honestly
from a machine that has never seen OpenShell. Testing it on the developer's Mac is a one-shot
experiment: the second run is no longer a first run.

**Clean room:** an ephemeral macOS VM created with [Lume](https://github.com/trycua/cua) (MIT).
`scripts/devenv/cleanroom.sh` clones a golden image, waits for SSH, and prints the connection
details; a companion `--destroy` deletes it. Lume's presets already create a user, enable SSH, and
disable sleep and screen locking, which is the plumbing this needs.

**How an agent uses it:** the subagent runs on the host and executes every step over
`ssh cleanroom '<command>'`. Each command and its output appears in the transcript, so a failed
setup step is readable rather than buried in a VM console. The loop is: clone → run
`factory contained setup` from scratch → assert `verify` passes → destroy.

Tart was the alternative and is more proven in CI, but it is Fair Source and would need an
org-level licensing answer; Lume is MIT and has none.

**Unverified:** Apple's EULA is widely cited as permitting only **two** macOS guests per host. Check
before designing anything that runs clean rooms in parallel.

A Linux container clean room is *not* sufficient here: the setup path's failures are macOS-specific
(podman machine, `host_gateway_ip`, certificate paths, the native gateway binary).

## 10. Open items

**F1 — resolved 2026-08-01: no permutations.** `--division` strictly follows `--target`. Exactly two
shapes are expressible — local runtime with the local division, k8s runtime with the k8s division.
`--target local --division k8s` is retired even though it was verified working on 2026-07-30.

What that retires with it: the stdio bridge and the Host-rewriting proxy. Both existed only because a
*sandboxed* agent had to reach `kubernetes-mcp-server` across OpenShell's egress proxy. Under
`--target k8s` that server runs inside the pod on loopback with nothing in front of it, so neither
the OAuth discovery denial nor the Host-header validation can occur — PLAYBOOK §5.4 and ATTEMPTS
28–33 become historical rather than operative, and neither mechanism should be reimplemented.

`host_rewrite_proxy.py` was operator setup with no code depending on it, and is deleted now.
`factory/templates/mcp_stdio_bridge.py` stays until phase 4 replaces the k8s division, because
`factory/division.py` still loads it; removing it earlier would break the current implementation for
no gain. Both remain in git history and on `backup/eval-harness-2026-08-01`.

**F2 — port-exposing subcommands.** `factory dashboard` binds :8420 inside the runtime.
`openshell sandbox create --forward` and `oc port-forward` are the levers; neither is wired. <Decision: No need to support dashboard for now.>

**F3 — iteration latency.** Pod startup adds roughly 15–30s per build iteration versus a local
`podman build`. Acceptable for cycles measured in minutes, but measure rather than assume. <Decision: OK>

**F4 — Apple's 2-VM-per-host limit** (§9), unverified. <Decision: no need to worry about this.>

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Local division opens an unauthenticated podman control plane on all interfaces | High | Accepted by decision (D1), first iteration only (§5.2). Loud warning at start, guaranteed shutdown at exit. |
| A developer's secrets are copied onto cluster storage | High | Gitleaks scan with warn-and-confirm before every k8s upload (§4.5). |
| `contained` guarantees differ local vs k8s | Medium | Stated in §1.2 and in `--help`, not buried. |
| OpenShell is alpha with an unstable surface | Medium | Pinned version. All CLI knowledge stays in `factory/openshell.py`. |
| A credential now exists in the cluster | Medium | User-created, namespace-scoped, referenced by name; the factory never handles the material. |
| Sidecar boundary bypassed via `pods/exec` | Medium | The verb is excluded from the Role, and `verify` asserts its absence. |
| Workspace copy diverges from the host tree during a long run | Medium | `sync` reports the branch and merge command; nothing merges automatically (§3.2). |
| In-sandbox eval scores incomparable to host scores | Medium | Loud launch warning; consider tagging in-sandbox experiment records. |

## 12. Testing

- **Unit:** argv passthrough after `--`; path rewriting (in-project rewritten, out-of-project left
  alone, no-op when paths coincide); policy generation (allow-wrapping, `binaries`,
  `allow_all_known_mcp_methods`); bundle YAML; `verify`'s missing-permission messages.
- **Dry run:** `FACTORY_OPENSHELL_DRY_RUN=1` composes the same argv the real path runs — the existing
  property that stops dry-run output from drifting. Add the k8s equivalent.
- **Clean room:** `factory contained setup` from a fresh macOS VM, asserted by `verify` (§9).
- **Local integration:** §3.6.
- **K8s integration:** §4.7.
- **Division integration:** §5.5 and §6.6.
- **Regression:** existing `factory tmux` behavior unchanged.

## 13. Phasing

Each phase ships as its own PR and is complete only when its verification block has been run and its
output pasted into the PR.

| Phase | Contents | Evidence it owes |
|---|---|---|
| 1 | `setup`/`verify`, passthrough surface, copy-mount, path rewriting, tmux + attach/ls/rm/sync | §3.6 steps 1–5, plus a clean-room run of §9 |
| 2 | Local division: managed `podman-mcp-server`, tool policy, brief | §5.5 steps 1–4 |
| 3 | K8s runtime: image, pod, PVC + tarball transport, secret scan, bundle + verify, attach | §4.7 steps 1–5 |
| 4 | K8s division: MCP server in-pod, build sidecar, validation-pod Role, brief | §6.6 steps 1–5 |

Phases 1–2 need no cluster. Phase 3 is the prerequisite for phase 4.

## 14. Out of scope

- Multi-tenant or shared-gateway OpenShell deployments.
- Replacing the runner abstraction; `contained` composes with it.
- Multi-pod integration testing by the agent (E4).
- Plain-Kubernetes builds; the division is OpenShift-only (E2).
