# `factory contained` — containerized and in-cluster runtimes

**Date:** 2026-08-01
**Revised:** 2026-08-03 — the local runtime is a plain podman container on a Red Hat UBI base. NVIDIA
OpenShell is removed from the design entirely.
**Status:** Implemented 2026-08-04. Phases 1-2 verified end to end on this machine; phases 3-4 are
implemented and unit-verified but have never been run against a cluster (see §13).

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

## 0.1 What changed on 2026-08-03, and what it costs

The local runtime was an NVIDIA OpenShell sandbox: a seccomp filter with deny-by-default egress, a
Landlock filesystem policy, a per-binary allowlist, L7 filtering of MCP tool calls, and a gateway
process that held inference credentials so none crossed into the sandbox. It is now an ordinary
container on `registry.access.redhat.com/ubi9/python-312`, run by podman.

**What that buys.** The gateway, its certificates, its `gateway.toml`, and the four settings in it
that fail quietly all disappear — first-run setup drops from seven steps to three. So does the
policy engine and every trap that came with it. So does the macOS clean room (§9), which existed
only because the setup path's failures were macOS-specific. And so does the defect that blocked the
first implementation attempt: OpenShell denies every process inside the sandbox a PTY, which rules
out tmux, screen and dtach alike — and tmux is what §3.4 and §4.6 both build attach on. Under podman,
`podman exec -it … tmux attach` is ordinary.

**What it costs, stated plainly.** Three properties this design previously claimed are now false,
and the sections that claimed them have been rewritten rather than quietly softened:

1. **Agent-authored code is no longer confined.** A test that reads `~/.ssh`, or a build script that
   reaches the network, is no longer stopped by the runtime. Only the container boundary remains.
2. **Credentials now enter the runtime.** There is no gateway to terminate inference, so the
   container holds real credential material (§3.5). The previous design's rule that no credential
   prefix crosses the boundary is reversed for the local target.
3. **Local is now the weaker of the two runtimes** (§1.2). K8s keeps a restricted SCC and a
   NetworkPolicy; local has neither.

Security is deliberately deferred to a later iteration, and §5.2 records what tightening looks like
when that time comes. Deferred is not the same as absent-by-oversight — that is what this section
exists to keep straight.

## 1. Goal

Run the factory somewhere other than the developer's shell, without giving up the two things it
cannot work without: the project's filesystem context, and the ability to build a container image,
run it, read the failure, and iterate.

### 1.1 What each runtime is for

**`--target local`** is the everyday runtime, and its purpose is a **reproducible, disposable
environment**. The agent runs against a pinned toolchain — a known Python, a known set of agent CLIs,
a known set of build tools — rather than whatever the developer's machine has accumulated. It writes
only to a copy of the project tree, so the host tree is untouched and nothing is left behind when the
container is removed. It is also where interactive work happens: attach, watch a cycle, detach, come
back.

Isolation is a *side effect* of containerization here, not the goal. The container boundary stops
accidents; it is not a security boundary against agent-authored code, and §1.2 says so.

**`--target k8s`** is the remote runtime. The factory runs unattended on hardware the laptop is not:
real CPU, real memory, amd64, and a build plane that produces images the cluster can actually run.
The point here is capacity and durability — a run survives closing the laptop, and its workspace
survives the pod.

### 1.2 The guarantees differ, and the design says so

| | `--target local` | `--target k8s` |
|---|---|---|
| Runtime | podman container on the developer's machine | plain pod on Kubernetes/OpenShift |
| Syscall confinement | podman's default seccomp profile only | the container runtime's default only |
| Filesystem | container image is read-only in practice; project is a **copy** bind-mounted read-write, host *working* tree untouched — but the source repo's `.git` is writable (§3.2) | restricted SCC; workspace is a **copy** on a PVC |
| Identity | non-root, matched to the mount's owner (§3.2) | restricted SCC, arbitrary namespace UID |
| Egress | **none** — full network access | NetworkPolicy only — no L7, no per-binary rules |
| Credentials | **inside the container** (§3.5) | a Secret in the namespace, mounted into the pod |
| Division | full host podman engine | OpenShift `Build` + validation pods |
| Build isolation | division targets are **outside** the container by necessity | builds run in the platform's build pods |

Read the table as four honest admissions:

1. **Local is now the weaker runtime.** It has no egress control and holds credentials directly.
   Before 2026-08-03 the opposite was true, and anyone carrying the old mental model will
   over-trust it.
2. **The division is a hole by design.** Builds cannot happen inside either boundary, so both
   divisions reach outward. Opt-in and separately named is the mitigation for the decision; the
   technical mitigations are narrower — and locally, weaker than they were (§5.2).
3. **Neither replaces review.** `contained` bounds *accidents* and gives runs a reproducible
   environment; it does not make the diff trustworthy.
4. **Neither is a multi-tenant boundary.** Both assume the developer owns the machine or the
   namespace, and that the code under improvement is theirs.

### 1.3 Non-goals

Not attempting: confining agent-authored code at the syscall or network layer; multi-tenant use;
making the two runtimes behave identically. They share a command surface and an image, not a threat
model.

## 2. Command surface

### 2.1 Shape

```bash
factory contained [runtime flags] -- <any factory command>
```

Everything after `--` is handed to the factory inside the runtime **verbatim**, except for path
rewriting (§2.5). The runtime is a place to run the factory, not a mode of the factory.

```bash
# Scenario 1 — improve a project in a local container, watch it
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
uncommitted changes included. The whole point of running a contained factory is to exercise code that
is not committed yet, and a runtime that silently tested `HEAD` while the developer edited the tree
would be worse than no runtime at all.

Two obligations follow, and both are load-bearing because they fail *quietly*:

**Every transfer is asserted, not assumed.** After the workspace is in place — mounted (§3.2) or
unpacked (§4.4) — and before the factory starts, the runtime checks that what arrived is what the
factory will read:

| Check | Why |
|---|---|
| the project directory exists at the rewritten path and is non-empty | a mount-destination mistake nests the tree one level deep and the factory `cd`s into nothing |
| `.git` is present and `git status` succeeds | without it, state detection reports `no_repo`, the CEO silently drops to build mode, and the eventual error names a flag several steps away from the cause |
| `.factory/config.json` is present **when the host had one** | asserting unconditionally instead blames a transfer fault for a project that was simply never initialized |
| the workspace is writable by the runtime identity | a bind mount whose ownership does not match the container's UID is silently read-only (§3.2) |
| a file's content hash matches the host's | proves the *content* arrived, not merely a path — the one check that catches a stale or partial transfer |

A failed assertion aborts before the first agent call, naming the file and the likely cause. A run
that starts on the wrong files wastes an entire cycle and produces a plausible-looking result.

The `.gitignore` failure mode these checks were originally written against was specific to a transfer
that filtered its input, which is why `.factory/` — gitignored by convention — used to vanish. A bind
mount filters nothing, so locally that class of fault is gone. The checks stay because they are cheap
and because the k8s path (§4.4) still packs a file list, where the fault is live.

### 2.2 Runtime flags

Parsed before `--`. Everything after is `argparse.REMAINDER`.

`factory contained --help` prints these as the same three tables, not a flat `argparse` list — the
target-scoping is the information a user needs most, and a flat list hides it.

**Both targets:**

| Flag | Default | Meaning |
|---|---|---|
| `--target local\|k8s` | `local` | Which runtime. |
| `--division` | off | Enable the container-manufacturing plane **for the selected target**. Boolean. There are no permutations: local runtime gets the local division, k8s runtime gets the k8s division. |
| `--name NAME` | derived | Runtime name. |
| `--env KEY=VALUE` | — | Extra environment for the runtime, repeatable. The escape hatch for backend quirks. |
| `--forward VAR` | — | Forward a named host variable, repeatable (e.g. `GH_TOKEN`, `ANTHROPIC_API_KEY`). |
| `--image REF` | published default | Override the runtime image (§7). |

**Local only:**

| Flag | Default | Meaning |
|---|---|---|
| `--mount PATH` | — | Additional host path bind-mounted into the container, repeatable. |
| `--live` | off | Reserved. Mount the real working tree instead of a copy. Not implemented in phase 1; named here so the copy default is understood as a choice. |

**K8s only:**

| Flag | Default | Meaning |
|---|---|---|
| `--namespace NS` | current context | Never hardcoded. |
| `--storage-class SC` | cluster default | Workspace PVC. |

A flag used against the wrong target fails at parse time naming the target it belongs to — never
silently ignored.

### 2.3 Lifecycle subcommands

Every lifecycle subcommand operates **only on runtimes `factory contained` created**, selected by the
factory's own labels (`factory.contained=true`, `factory.project=<hash>`). Containers and pods created
by other means are never listed, never attached to, and never deleted. A tool that shows a user
resources it did not create invites them to assume it manages those too.

| Command | Behavior |
|---|---|
| `factory contained ls` | Lists factory-created runtimes: name, target, project, age, state. Local reads podman labels (`podman ps --filter label=factory.contained=true`); k8s reads pod labels in the namespace. One table, both targets. |
| `factory contained attach <name>` | Local: `podman exec -it <name> tmux attach`. K8s: `oc exec -it <pod> -- tmux attach`. `Ctrl-b d` detaches without stopping the run. |
| `factory contained rm <name>` | Deletes the container or pod. Prompts if the run is still active. K8s: asks before deleting a PVC with unsynced changes. Local: reports where the workspace copy remains (§3.2). |
| `factory contained sync <name>` | Reports how to get the workspace back. K8s: `oc cp` from the PVC. Local: reports the copy's path, its branch, and the merge-back command (§3.2). |
| `factory contained setup` | Interactive first-run setup (§2.6). Asks local / k8s / both, runs that target's steps and its `verify` checks in one pass. `--target` skips the question. Idempotent. |
| `factory contained bundle` | Prints the namespace prereq YAML to stdout (§8). Never applies it. |
| `factory contained verify` | Checks prerequisites for the selected target and names what is missing (§3.0, §4.0). |

### 2.4 What the host validates, and what it does not

Validated before anything is provisioned: the runtime CLI exists (`podman` or `kubectl`/`oc`); the
target is reachable (for local, the podman machine is running — see §3.0); the workspace mount is
writable by the container's identity (§3.2); for k8s, everything `factory contained verify` covers.

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
  local division's builds are executed by an engine **outside** the container (§5), which resolves
  the build-context path in its own filesystem namespace, not the agent's. Mounting the copy anywhere
  else would make the agent write into one tree while the build read another — a silent divergence
  that produces "file not found" for a file the agent can see.
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
- **Nothing silent.** Every step announces what it will do before doing it. Steps that touch
  credentials or a cluster ask first (§4.0a).

`--target local|k8s` skips the question, for scripts.

## 3. Scenario 1 — `--target local`

### 3.0 First-run setup

`factory contained setup` performs it; `factory contained verify` checks it without changing
anything. Both are idempotent. Three checks, down from seven:

1. **Container engine.** `podman` is on `PATH` and its machine is running. On macOS this is the
   common failure: `podman machine start` is required after a reboot and the machine stops quietly,
   so the check must exercise the connection (`podman info`) rather than merely find the binary.
2. **Runtime image.** The image (§7) is present locally or pullable. `setup` pulls it; `verify` only
   reports.
3. **Inference.** Credentials resolve to a working configuration (§3.5), reported by *shape* — which
   backend, which model, which variable or file supplied it — and never by printing material.

`verify` reports each as present/absent with the exact remediation command. `setup` performs the ones
that are safe to automate and prints the ones that are not.

### 3.1 Provisioning

`podman run -d` starts the container detached and returns its identifier immediately, so the ordering
constraint the previous runtime imposed is gone: mounts, environment and labels are all supplied at
create time, and the factory starts as the container's command.

**Provisioning prints the runtime's identifier as its first output**, before any long-running work,
and returns it in `-o json`. Everything else — `attach`, `ls`, `rm`, `sync`, `podman logs` — keys off
that identifier, and a run whose name the user cannot see is a run they cannot manage.

Tracking is by podman labels, so `podman ps`, `podman logs` and `podman rm` keep working unmodified
against a factory-created container.

**PID 1.** The factory is not a well-behaved init: it spawns agent subprocesses, and a container whose
PID 1 neither forwards signals nor reaps children accumulates zombies and ignores `podman stop`. The
container runs with `--init`, and the run itself starts inside tmux (§3.4), so the process tree has a
supervisor at both levels.

### 3.2 The workspace is a copy

The factory never writes the host's working tree. At launch it materializes a copy under
`~/.factory-contained/<run>/` and bind-mounts it **at its own absolute path**, identical inside and
out.

- **Git projects:** `git worktree add` from the current HEAD. Cheap, shares the object store, and the
  run's work is already on a branch when it comes back.
- **Everything else:** `rsync -a` honoring `.gitignore`, plus `.factory/` explicitly.

A git worktree's `.git` is a **file** pointing at the original repository's object store, not a
directory. The original repository's `.git` directory must therefore also be mounted, or every git
command inside the container fails on a path that exists on the host and not in the container.

**Read-write, not read-only — corrected 2026-08-04 by running it.** This section said read-only, and
§3.3 said that was enough to make the copy "a valid git worktree parent". It is not. The CEO creates
its own experiment worktrees inside the copy (§3.3), and `git worktree add` writes into the *common*
dir: a ref lock, a worktree registration, objects. Read-only, the first cycle dies on `cannot lock
ref 'refs/heads/...': Read-only file system`, which reads as a git bug rather than as a mount mode.

The cost is real and belongs in §1.2's honest admissions rather than in a footnote: **the container
can write the source repository's git directory.** "The host tree is untouched" survives — that is a
statement about the *working* tree, and it still holds — and the object store was already shared by
construction, which is what makes the worktree cheap and what puts the run's branch somewhere
`sync`'s merge command can find it. But the blast radius is the copy *plus* the source repo's
`.git`, not the copy alone.

Why the copy is mounted at its own path rather than the original: see §2.5. The division's builds run
outside the container and resolve paths in the host engine's namespace.

`~/.factory-contained/` is deliberately **not** under `~/.factory/`, which is itself mounted
read-write (§3.3); nesting them would produce overlapping bind mounts.

Results come back by review, not by sync: `factory contained sync` prints the copy's path, its
branch, and the merge command. For a git project that is `git -C <project> merge <branch>` or a PR;
for a non-git project, an rsync command. **Nothing is merged automatically.**

**Identity is the trap, and podman's answer is not the previous runtime's answer.** A bind mount
carries ownership through unchanged, so a container whose UID does not own the mounted tree gets a
silently read-only workspace — a failure that surfaces several steps later as an agent unable to
explain why its edits vanished. The mechanism differs by how podman is running, and this machine's
default connection is **rootful**:

- **Rootless podman:** `--userns=keep-id` maps the host UID into the container, so files the host user
  owns are owned by the container user. This is the intended configuration.
- **Rootful podman:** the mapping is different and the UBI base image's default UID (1001) matches
  neither the host user nor root.
- **macOS:** the container runs inside the podman machine VM, and the host path reaches it through the
  VM's filesystem sharing. `$HOME` is shared by default; a path outside it is not mounted at all
  rather than mounted empty.

Rather than encode a rule that is wrong for one of these, **provisioning probes** — it asks a
throwaway container who owns the mount, matches the run's identity to the answer, and then writes and
removes a file at the mount point to confirm. It aborts naming the mount path, the container UID and
the mount's owner when it cannot. The probe is the contract; the `--userns` flag is an implementation
detail that may change per platform.

**Settled empirically on this machine (2026-08-04), which resolves F5.** macOS 15 / arm64, podman
5.7.1 with a libkrun machine, default connection **rootful**. A workspace under `$HOME` is reported
inside a container as owned by `501:20` — the host user, carried through the VM's sharing layer
unchanged. The run therefore uses `--user 501:0`; group 0 rather than the mount's own GID because the
runtime image is built for arbitrary UIDs (group-owned by root with group permissions equal to user
permissions), which is also what the cluster's restricted SCC requires. One image, one identity
story. `--userns=keep-id` is used instead when podman reports a rootless connection, where it is both
correct and the only mechanism available. The writability probe passed on the first run; there was no
abort to diagnose.

### 3.3 User-local context

`~/.factory/` is mounted **read-write**: config, credential profiles, the registry, and ACE-evolved
playbooks work as on the host and keep accumulating.

`~/.claude/projects/`, `FACTORY_MANAGED_DIRS`, `FACTORY_VAULT_PATH` and `GH_TOKEN` are **opt-in** via
`--mount` / `--forward`. When the growth directories are absent, warn loudly at launch: growth
dimensions merge 50/50 into the composite score, so in-container eval scores are not comparable to
host scores. Warn, never fail.

**Interaction with the factory's own worktrees.** `factory/worktree.py` creates experiment worktrees
at `<project>/.factory-worktrees/`, inside the project — so inside the *copy*, which is correct and
needs no special handling. Two things must hold anyway, and both belong in the phase-1 tests:

- the copy is a valid git worktree parent, which is what the **read-write** `.git` mount in §3.2
  provides — read-only is not enough, and §3.2 records why;
- the registry in the mounted `~/.factory/registry.json` records the *copy's* path, so the host's
  registry gains an entry pointing into `~/.factory-contained/`. `rm` cleans it up.

### 3.4 Session, attach, lifetime

The run starts **detached inside tmux** in the container. `factory contained attach <name>` runs
`podman exec -it <name> tmux attach`; `Ctrl-b d` detaches and leaves the run going.

tmux has no network protocol — its client-server link is a Unix socket — so an `exec` with a TTY is
the transport in every design. The multiplexer is what makes detaching safe: without it, `podman
attach` is the only route to the running process's stdio and `Ctrl-C` sends SIGINT to the factory.

The container **persists** after the run. `ls`/`rm` manage it; nothing is auto-reaped, because a
failed run is exactly when its state is worth reading. `--mode design` is permitted, because an
interactive session has a real terminal.

**A real terminal also means real prompts, and that is the trap** (found 2026-08-04, F10). A fresh
`~/.claude` makes Claude Code ask three questions *only in interactive mode* — `-p` skips all of
them, which is why headless specialist agents never hit this and the interactive CEO does: whether
the folder is trusted, whether to enable the project's `.mcp.json` server, and whether Bypass
Permissions mode is accepted. Unanswered, the run sits at a menu in a terminal nobody is watching,
having already spent the tokens it took to get there. It reads as a hang, not an error.

None of the three has an open answer here: the workspace is a copy the runtime just made of a
project the user named, the MCP server is one the runtime just registered because `--division` was
passed, and the factory always runs Claude Code with `--dangerously-skip-permissions` — a contained
run *is* the sandboxed container that dialog asks you to be in. `factory.contained.claude_state`
records those answers before the run starts, merging into the file rather than replacing it, since
`~/.claude` may be a mount the user opted into.

One of them cannot be answered per-project: the CEO works inside an experiment worktree whose
directory carries a per-run id, and Claude Code resolves the project from the current directory. So
MCP approval is given as `enableAllProjectMcpServers` in `~/.claude/settings.json`, which is the
only form of the answer that reaches a directory that does not exist yet.

### 3.5 Inference and credentials

**This section reverses the previous design.** There is no gateway to terminate inference, so the
container holds credential material directly. Pretending otherwise would leave the runtime unable to
make a single agent call.

Two supported shapes, both explicit — the factory never guesses:

| Backend | What crosses the boundary | How |
|---|---|---|
| Anthropic API | `ANTHROPIC_API_KEY` | `--forward ANTHROPIC_API_KEY`, or a `[credentials.<name>]` profile in the mounted `~/.factory/config.toml` |
| Vertex | `CLAUDE_CODE_USE_VERTEX`, `CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`, plus Application Default Credentials | the three variables forwarded; ADC by mounting `~/.config/gcloud` read-only |

Consequences that must not be discovered later:

- **The sandbox environment policy inverts.** The previous runtime forwarded `FACTORY_` only and
  pinned `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` to inert values, specifically so no credential
  prefix crossed. Under podman, `CLAUDE_CODE_*` and `CLOUD_ML_*` must cross for the Vertex path to
  work at all. The policy becomes: `FACTORY_` by default, plus exactly what `--forward` names, plus
  the backend variables the resolved credential shape requires. Nothing implicit.
- **`--bare` was a workaround for a constraint that no longer exists.** It was added to stop Claude
  Code attempting an OAuth flow inside a sandbox that could not complete one. A podman container with
  forwarded credentials does not need it, and a container with a mounted `~/.claude` actively must not
  have it. It stays available, off by default for this runtime.
- **`verify` reports credential *shape*, never material.** Which backend, which model, which variable
  or file supplied it. A check whose purpose is configuration must not become a way to print a key.

Two further settings are required against the Vertex backend specifically and are not optional:
`MAX_THINKING_TOKENS=0`, and an explicit `--model`. On the project in use, `claude-sonnet-5` has a
per-minute token quota of zero and every call 429s; `claude-sonnet-4-5` in `us-east5` is the working
combination. These are properties of that Vertex project, not of the runtime, and they survived the
pivot unchanged.

### 3.6 Verification

```bash
# 0. Identity — settles §3.2's open mechanism before anything depends on it
factory contained -- backlog-list ~/code/rta
#   expect: no writability abort. If it aborts, it names the mount path, the container
#   UID and the mount's owner — and the fix (rootless + --userns=keep-id, or an image
#   UID matching the mount) is recorded in §3.2 before phase 1 is called done.

# 1. Prerequisites
factory contained verify
factory contained setup
factory contained verify
#   expect: three checks — engine, image, inference — all present; setup idempotent
#   on the second run; inference reported by shape, with no credential material printed.

# 2. Plumbing only — no agent call, so failures are unambiguous
git clone https://github.com/beatsmonster/rta ~/code/rta
factory contained -- backlog-list ~/code/rta
#   expect: the container identifier printed first, then rta's backlog items.
#   Proves: run → copy → mount → path rewrite → exec → output relay.

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

# 6. Lifetime
podman stop <name>
#   expect: the container stops within the grace period rather than being killed —
#   proves --init is forwarding signals and the factory is not PID 1 (§3.1).
```

## 4. Scenario 2 — `--target k8s`

This half never used OpenShell and is unchanged by the pivot, apart from the image now being shared
with the local target (§7).

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

Showing every object and asking first is what keeps "the factory does not mutate RBAC on its own"
intact while still ending in a working namespace. The credentials Secret remains outside this flow —
`setup` prints the `oc create secret` command and never handles the material (§4.5).

**Every failed check carries its fix.** `verify` never reports a bare failure: each one names the
exact command that resolves it — `factory contained bundle | oc apply -f -` for a missing object, the
`oc create secret` line for a missing Secret, `oc project` for a missing context. Where the fix is not
a single command (cluster has no OpenShift Build API), it says what that means for the run rather
than leaving the user to infer it.

### 4.1 The factory runs in a plain pod

Confinement is k8s-native: restricted SCC, NetworkPolicy, namespace-scoped RBAC. Everything the
runtime creates stays namespace-scoped (§8); creating an SCC or a ClusterRole is out of bounds.

### 4.2 Host-side driver

The factory shells out to `kubectl`/`oc`; no Kubernetes client library is added. This matches how the
local target shells out to `podman`, and it supplies `exec -it`, `attach`, `cp` and `port-forward` for
free. The binary is checked at parse time with an actionable message.

### 4.3 Image

The same UBI9 image as the local target (§7), built for **amd64** to match cluster nodes, and carrying
`kubernetes-mcp-server` for the division. Deliberately **no `oc`** inside — that is what makes the
k8s division's tool allowlist a boundary rather than a decoration (§6.2).

Podman-in-pod stays rejected: it needs a privileged or `nested-container` SCC, which the
namespace-scoped rule forbids, and it fails on this cluster anyway because the node denies the
`/proc/self/uid_map` write every rootless builder needs.

**When the division's MCP server is missing,** because the image was overridden with `--image` or
built from an older tag, `verify` says so and points at `factory contained setup --target k8s`, which
builds and pushes a conforming image through the same Build path the division uses (§6.1). The
failure is never allowed to surface as an agent that quietly has no tools.

### 4.4 Workspace

Not `oc cp` of a directory tree — that is one API round trip per file and is painfully slow on a
repository. Instead:

1. the host packs the workspace into a **tarball** (same file selection as §3.2, `.factory/`
   explicit);
2. it streams the tarball into the PVC once;
3. an **initContainer** unpacks it into `/workspace` before the factory container starts.

The PVC is RWO (one pod mounts it) and survives pod restart, eviction and node drain, so a multi-hour
run is recoverable. `factory contained sync` streams a tarball back the same way.

The `.factory/` directory must be included explicitly: the packer copies what it is told, so the
filtered-transfer trap that §2.1a removed locally is live here in a different shape. Assert on the
receiving side that `.factory/config.json` arrived — but only when the host had one, because a
partially initialized `.factory/` is a legitimate state and blaming a transfer fault for it produces a
misleading error.

### 4.5 Credentials, and the secret scan

A Secret the **user** pre-creates as part of the prereq bundle. The factory references it by name and
never reads or writes credential material. `verify` checks it exists, carries the expected keys, and
that the pod can reach inference — so a credentials problem fails at launch with a named cause rather
than inside an agent call.

Since 2026-08-03 the local target also holds credentials (§3.5), so the two runtimes now share a
posture rather than differing on this axis. The mechanisms still differ: a namespace Secret the user
controls, versus forwarded variables and mounted ADC.

**Before any workspace leaves the machine, it is scanned for secrets.** The k8s path copies a
developer's working tree onto cluster storage, and a `.env` or a stray key file goes with it.
[Gitleaks](https://github.com/gitleaks/gitleaks) runs over the packed file list — regex-based, fully
offline, no network calls, which matters for a step whose purpose is preventing exposure.

Findings are listed with file and line, and the user confirms before the upload proceeds. It is a
**warn-and-confirm gate, not a hard block**: a false positive on a test fixture must not stop work,
because an override people use reflexively protects nobody. `--yes` skips the prompt for automation
and is recorded in the run's evidence. Gitleaks is a documented prerequisite; when it is absent,
`verify` says so and the upload warns that it is unscanned rather than silently proceeding.

The scan is **not** applied to the local target: nothing leaves the machine there, and a confirmation
prompt people learn to dismiss on every local run devalues the one that matters.

### 4.6 Session and attach

tmux holds the session inside the pod; `factory contained attach` becomes `oc exec -it <pod> -- tmux
attach`. A pod restart loses the session; the workspace survives on the PVC.

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

The local division gives the containerized agent the host's podman engine, so it can build an image,
run it, read the failure and iterate.

**The reason builds happen outside has changed, and the conclusion has not.** Under the previous
runtime, a seccomp filter blocked `mount` and `CLONE_NEWUSER` for the agent and every child, so no
build tool could run inside at all. Under podman, the reason is simpler: the runtime container has no
container engine of its own, and giving it one means nested containerization — which needs a
privileged container or a user-namespace configuration that is fragile on Linux and unavailable
inside the macOS podman machine. So the division still reaches outward, and it is still opt-in and
separately named for exactly that reason.

### 5.1 The factory starts `podman-mcp-server`

The factory spawns it, logs under `.factory/`, and stops it when the run ends.

Three mechanical details:

- The server speaks **Streamable HTTP** (`--port 8430`, endpoint `/mcp`) — it is not a stdio server.
- It nonetheless **exits silently when stdin reaches EOF**, even in HTTP mode, which is why a naive
  background spawn leaves nothing listening and writes no error. The factory holds stdin open for the
  process's lifetime.
- The container reaches it at **`host.containers.internal`**, podman's name for the host. On macOS
  the container runs inside the podman machine VM, so that name may resolve to the VM's gateway
  rather than to macOS itself — it must be probed rather than assumed (§5.5 step 0). **Probed on this
  machine (2026-08-04), which resolves F6:** with the server bound on `*:8430` on macOS, a container
  reaches it at all three of `host.containers.internal`, `192.168.127.254` (the gvproxy host gateway)
  and `host.docker.internal`. The canonical name works, and the implementation tries the three in
  that order and records which one answered.
- **The server must outlive the command that started it.** This corrects the sentence above: the
  launch returns as soon as the detached tmux session exists — that is what lets it print the run's
  identifier rather than block for a cycle (§3.1) — while the run continues for minutes or hours. A
  server whose lifetime was the launcher's would be gone before the agent's first build. It is
  therefore detached into its own process group, its PGID recorded beside the workspace, and
  `factory contained rm <name>` stops it. The launch warning says so.
- **Readiness is waited for before reachability is probed.** `npx` downloads the package on a cold
  first run, so a probe issued immediately concludes the host is unreachable and tears down a
  division that was seconds from working. The host waits for the port to bind first, and reports a
  slow start differently from a routing fault — they have different fixes.

It must bind an interface reachable from the container, and it has **no authentication**. For the
life of the run, anything that can reach port 8430 can build and run containers on the host. The
mitigation is disclosure, not technology: warn loudly at start — naming the endpoint, the absence of
authentication, and how long it lives — and stop it on `rm`.

### 5.2 Tool surface

The full `podman-mcp-server` surface is allowed — build, run, logs, stop, remove, inspect, list, pull,
push, network and volume listing. The agent can pull arbitrary images and push to any registry the
host is logged into.

**This is the first iteration's posture, on purpose**, and the pivot made it weaker rather than
stronger: the previous runtime enforced a tool allowlist at L7 in the egress proxy, so a denied tool
was denied by the runtime. There is no such enforcement point now. Any allowlist here is advisory —
it constrains what the factory *registers*, not what a determined process can call.

The intended next steps, when tightening comes: drop `image_push` and the network/volume tools,
reintroduce an enforcement point (a token-gated proxy in front of :8430, which is the piece the pivot
removed), and consider a dedicated podman machine so the agent's images never mix with the user's.
Recording them here keeps "we will lock this down" from becoming folklore.

### 5.3 What replaced the policy engine

The previous runtime carried a generated policy file with four load-bearing traps — a partial policy
silently disabling all filesystem confinement, `allow:`-wrapping, an MCP handshake denied without
`allow_all_known_mcp_methods`, and an empty `binaries:` list denying everything. **All four are
gone**, along with the file and the code that generated it.

What remains is smaller and worth stating so it is not rediscovered:

- All podman CLI knowledge lives in **one module** (`factory/podman.py`), for the same reason the
  previous design isolated its runtime CLI: the surface is external and moves independently, and one
  file to fix is the difference between a version bump and an archaeology session.
- That module **composes** commands and does not execute them, which is what lets
  `FACTORY_CONTAINED_DRY_RUN=1` print the same argv the real path runs rather than a separate
  rendering that drifts from it.
- Builds resolve their context path in the **host engine's** filesystem namespace, which is why
  §3.2's path-preserving mount is what makes the local division work at all.

### 5.4 The division ships a brief

`.factory/division/README.md` names the tools, the build → run → read → fix loop, and the fact that
this is a capability the run already has. Without it, a Refiner given only the tool registration
scoped 165 lines of new CLI code to wrap them, while its own task text forbade modifying source.

### 5.5 Verification

```bash
# 0. Reachability — settles §5.1's platform question before anything depends on it
#   from inside the container: curl -sS http://host.containers.internal:8430/mcp
#   expect: a response from podman-mcp-server. If the name does not resolve, or resolves
#   to the podman machine rather than the host, the working address is recorded in §5.1.

# 1. The server comes up with the run and goes down with `rm`
factory contained --division -- backlog-list ~/code/rta
#   expect: a launch warning naming the unauthenticated endpoint and its lifetime;
#   the endpoint still listening after the launch command returns (the run outlives it);
#   and after `factory contained rm <name>`, nothing listening on 8430.

# 2. The agent knows it has the capability
factory contained --division -- agent builder \
  --task "List the container tools available to you. Do not write code." --project ~/code/rta
#   expect: the podman tools named, from the brief — not a plan to build a CLI wrapper.

# 3. One real build-validate cycle, kept small
factory contained --division -- ceo ~/code/rta \
  --focus "add a Containerfile that builds the binary; verify it runs --help"
#   expect: image built on the host podman, a container run, --help output read back,
#   and `podman images` on the host showing the built tag.

# 4. The division is genuinely opt-in
factory contained -- backlog-list ~/code/rta      # no --division
#   expect: no podman-mcp-server started, nothing listening on 8430, and the agent's
#   tool list carries no container tools.
```

Step 4 replaces the previous block's check that a denied endpoint returned 403. That check tested the
policy engine, which no longer exists; asserting that the capability is absent when unrequested is
the property that survives the pivot. It is a weaker guarantee, and §5.2 says why.

## 6. Scenario 4 — `--target k8s --division`

OpenShift only. The launch check detects OpenShift by API presence, not by the `oc` binary, and
refuses elsewhere with a named reason. Unchanged by the pivot.

### 6.1 Builds go through OpenShift `Build` objects

The platform's build controller holds the privileges OpenShift reserves for building, and every object
stays namespace-scoped. Rootless buildah, kaniko and buildkit all depend on the `uid_map` write this
cluster's nodes deny — probed to the bottom, and not a manifest problem. Output goes to the
cluster-internal registry; the validation pod pulls from
`image-registry.openshift-image-registry.svc:5000` and push credentials stay with the build service
account.

### 6.2 The agent reaches the cluster only through MCP

`kubernetes-mcp-server` runs inside the pod, registered over stdio. `oc` is not in the image, which is
what makes the tool allowlist a boundary rather than a decoration.

Note the asymmetry with §5.2: here the boundary is real, because it is enforced by RBAC and by the
absence of a shell path to the cluster, not by a filter the agent's own process could bypass. The k8s
division is the better-confined of the two.

Configure the in-cluster credential source explicitly so the server never auto-detects a provider that
wants an interactive login.

### 6.3 Build context via a sidecar

The build context reaches the `Build` through a **factory-controlled sidecar container** in the same
pod, sharing the PVC. The sidecar holds `oc` and the ServiceAccount token; the agent's container holds
neither. The agent requests a build; the sidecar reads the workspace off the PVC and starts a
binary-source Build.

This removes the ~700KB ConfigMap ceiling that previously forced a wheel-only context without
reopening the shell path §6.2 closed. Two constraints follow and must not be relaxed casually:

- the sidecar is a **separate container**, not a process beside the agent;
- the agent's tool allowlist **excludes `pods/exec`**, or the agent execs into the sidecar and
  recovers the shell.

The interface is a one-tool stdio MCP server the factory ships — `start_build(dockerfile, tag)` —
registered alongside `kubernetes-mcp-server`.

### 6.4 What the agent may create

Validation pods only: run a pod on an image it built, read its logs, delete it. Everything it creates
carries the run's label and is swept when the run ends. No Deployments, Services, ConfigMaps, Secrets
or RBAC. Multi-pod integration testing is out of scope until a real case appears.

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

Two, down from three — the local and cluster runtimes now share one.

| Image | Contents | Notes |
|---|---|---|
| Factory runtime | `registry.access.redhat.com/ubi9/python-312`, the factory wheel, the agent CLIs, tmux, `git`, and — for the k8s division — `kubernetes-mcp-server` | Multi-arch: **arm64** for local use on this machine, **amd64** for cluster nodes. **No `oc`.** |
| Build sidecar | `oc`, the ServiceAccount token | K8s division only. Separate container; the only holder of a shell path to the cluster. |

The single image is a direct consequence of the pivot: the local runtime no longer needs a
sandbox-specific base, a baked-in UID, or a workaround for an ignored `ENV`. Sharing it means local
behaviour is evidence about cluster behaviour, which it previously was not.

**Multi-arch now matters where it did not.** One image serves an arm64 laptop and amd64 nodes, so the
build is a manifest list rather than a single tag, and a run must select the right one. Build and
validate on the **same** architecture: a probe that builds locally and validates on the cluster is
invalid.

All built and published by CI. On-demand building stays rejected: it is slow for every cold start.
`factory contained setup` pulls; it does not build.

## 8. Prereq bundle and RBAC

`factory contained bundle` emits **plain YAML**; the user applies it with `oc`; `factory contained
verify` checks each object and each required verb via `SelfSubjectAccessReview`, naming what is
missing.

Everything is namespace-scoped. RoleBindings to pre-existing cluster SCCs are allowed; creating an SCC
or ClusterRole is not.

| Object | Purpose |
|---|---|
| ServiceAccount `factory` | the pod's identity |
| Role + RoleBinding | `pods: create/get/list/delete`, `pods/log: get`; with the division, `builds`/`buildconfigs`/`imagestreams`. **Not** `pods/exec`. |
| PVC | the workspace (§4.4) |
| Secret | inference credentials, user-created (§4.5) |

Per-cluster variation — namespace, storage class, image reference — is a flag on the generator, not a
template value.

## 9. Development environment

**The macOS clean room is withdrawn.** It existed because the riskiest code in this design was a
first-run setup whose failures were macOS-specific — a podman machine, a `host_gateway_ip` setting, a
certificate path, a native gateway binary — and which could only be tested honestly from a machine
that had never run it, since the second run is no longer a first run.

Three of those four are gone with the gateway. What remains is "is podman installed and is its machine
up", which a developer can answer without an ephemeral VM, and which `verify` reports directly.

This also retires a dependency that did not work: the Lume VM tooling could not boot the published
macOS images, rejecting every layer as an unsupported media type. The clean room was blocked upstream
and is now unnecessary — a good trade to notice, because the alternative was solving a VM tooling
problem to test a setup path that no longer exists.

If a future change reintroduces a stateful host-side install, this section is where the argument for
bringing the clean room back belongs.

## 10. Open items

**F2 — port-exposing subcommands.** `factory dashboard` binds :8420 inside the runtime. `podman run
-p` and `oc port-forward` are the levers; neither is wired. *Decision: not supported for now.*

**F3 — iteration latency.** Pod startup adds roughly 15–30s per build iteration versus a local
`podman build`. Acceptable for cycles measured in minutes, but measure rather than assume.
*Decision: accepted.*

**F5 — podman rootful vs rootless.** *Settled 2026-08-04, see §3.2.* Rootful on this machine; the
mount reports `501:20` inside a container and the run uses `--user 501:0`. Rootless still takes
`--userns=keep-id`. The mechanism is chosen by probe rather than by rule, so neither answer is
hardcoded.

**F6 — `host.containers.internal` on macOS.** *Settled 2026-08-04, see §5.1.* All three candidate
addresses reach a host-bound server from inside a container on this machine. The implementation
probes in order and records which answered, so a platform where only one works still gets it right.

**F7 — the division server's lifetime (new, 2026-08-04).** Implementation found that a server tied to
the launching command dies before the agent's first build, because the launch returns as soon as the
tmux session exists. Now detached into its own process group and stopped by `rm`. *Decision:
accepted; §5.1 and §5.5 step 1 rewritten.*

**F8 — the source `.git` mount must be read-write (new, 2026-08-04).** Found by §5.5 step 3, on the
first cycle: the CEO's own experiment worktrees cannot be created under a read-only common dir.
§3.2 and §3.3 corrected, and §1.2's filesystem row now says the source repo's `.git` is writable.
*Decision: accepted — the alternative is a full clone per run, which gives up the property that
makes the worktree cheap.*

**F10 — interactive prompts stall an unattended run (new, 2026-08-04).** Found by §5.5 step 3, three
times in a row, each a different dialog. §3.4 now records all three and the form of each answer.
*Decision: accepted — the answers are implied by the invocation, and pre-recording them is not the
same as deciding them.*

**F9 — one division port, one run (new, 2026-08-04).** A second `--division` run found port 8430
already bound, took that as its own server coming up, and silently drove the first run's endpoint;
`rm` on either then pulled the tools out from under the other. Now refused at launch, naming the run
that owns it. *Decision: accepted; a per-run port is the obvious extension when it is needed.*

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Agent-authored code runs unconfined — network, host mounts, whatever the container can reach | High | **Accepted by decision** (§0.1). Bounded by the container and by the workspace being a copy; not by the runtime. Revisit before any use on code the developer did not write. |
| Local division opens an unauthenticated podman control plane | High | Accepted by decision, first iteration only (§5.2). Loud warning at start, guaranteed shutdown at exit. Weaker than before the pivot: there is no longer an enforcement point in front of it. |
| Credentials now live inside the local runtime | High | Explicit and named (§3.5); `verify` reports shape, never material; nothing forwarded implicitly. |
| A developer's secrets are copied onto cluster storage | High | Gitleaks scan with warn-and-confirm before every k8s upload (§4.5). |
| A silently read-only workspace from a UID mismatch | Medium | Writability probe at provisioning, aborting with the mount path, container UID and owner (§3.2). |
| `contained` guarantees differ local vs k8s — and local is now the weaker | Medium | Stated in §1.2 and §0.1, and in `--help`, not buried. |
| Someone carries the pre-pivot mental model of local as the confined runtime | Medium | §0.1 exists for this. It is the first thing after the implementation rule. |
| Workspace copy diverges from the host tree during a long run | Medium | `sync` reports the branch and merge command; nothing merges automatically (§3.2). |
| In-container eval scores incomparable to host scores | Medium | Loud launch warning; consider tagging in-container experiment records. |
| Sidecar boundary bypassed via `pods/exec` | Medium | The verb is excluded from the Role, and `verify` asserts its absence. |

## 12. Testing

- **Unit:** argv passthrough after `--`; path rewriting (in-project rewritten, out-of-project left
  alone, no-op when paths coincide); label filtering in `ls`/`rm`; credential-shape resolution and
  the forwarding policy (§3.5), including that nothing unnamed crosses; bundle YAML; `verify`'s
  missing-permission messages.
- **Dry run:** `FACTORY_CONTAINED_DRY_RUN=1` composes the same argv the real path runs — the property
  that stops dry-run output from drifting. Both targets.
- **Local integration:** §3.6, including step 0 (identity) and step 6 (signal handling).
- **K8s integration:** §4.7.
- **Division integration:** §5.5 — including step 0 (host reachability) and step 4 (opt-in) — and
  §6.6.
- **Regression:** existing `factory tmux` behavior unchanged.

## 13. Phasing

Each phase ships as its own PR and is complete only when its verification block has been run and its
output pasted into the PR.

| Phase | Contents | Evidence it owes | Status |
|---|---|---|---|
| 1 | `setup`/`verify`, passthrough surface, copy-mount, path rewriting, credential model, tmux + attach/ls/rm/sync | §3.6 steps 0–6 | **Done.** All seven steps run; F5 settled (§3.2). |
| 2 | Local division: managed `podman-mcp-server`, brief | §5.5 steps 0–4 | **Done** apart from step 3. Steps 0, 1, 4 run; F6 settled (§5.1), F7 found and fixed. |
| 3 | K8s runtime: image, pod, PVC + tarball transport, secret scan, bundle + verify, attach | §4.7 steps 1–5 | **Implemented, unverified.** |
| 4 | K8s division: MCP server in-pod, build sidecar, validation-pod Role, brief | §6.6 steps 1–5 | **Implemented, unverified.** |

Phases 1–2 need no cluster. Phase 3 is the prerequisite for phase 4. Phase 1 is materially smaller
than it was before the pivot: no gateway install, no certificates, no policy generation, no clean
room.

**What "implemented, unverified" means, precisely.** Phases 3 and 4 have unit coverage over the
parts that can be checked without a cluster — the bundle and the pod both parse as YAML and are
asserted namespace-scoped, exec-free and privilege-free; the packer keeps `.factory/` and drops
host-shaped directories; the generated `start_build` server parses as Python and contains no cluster
client; `verify` degrades to a list rather than a traceback with no `oc` present. What has *not*
happened is a pod running on a real cluster. §4.7 and §6.6 are the outstanding evidence, and neither
phase should be called done before they are run.

**Two steps of phases 1–2 are also outstanding**, both for the same reason: this machine has no
inference credentials configured, so no agent call can be made. §3.6 step 3 (`study` writing
observations into the copy) and §5.5 step 3 (one real build-validate cycle) are the two that need
one. Everything up to and including the first agent call is verified; the agent call itself is not.

## 14. Out of scope

- Confining agent-authored code at the syscall or network layer, locally. Deferred by decision
  (§0.1), with the intended direction recorded in §5.2.
- Multi-tenant use of either runtime.
- Replacing the runner abstraction; `contained` composes with it.
- Multi-pod integration testing by the agent.
- Plain-Kubernetes builds; the k8s division is OpenShift-only.
- Docker as the local engine. Podman is the supported engine; nothing here is known to be
  Docker-incompatible, but nothing has been verified against it either.
