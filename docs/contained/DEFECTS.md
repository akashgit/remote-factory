# `factory contained` — defects found and fixed

Known issues in `factory contained`, and the defects already fixed in it. Written for whoever
maintains this next: **read [Still outstanding](#still-outstanding) first**, then
[Decisions that look like defects](#decisions-that-look-like-defects) so you do not undo them.

Every defect below was found by running the feature — against a real podman host, a real OpenShift
cluster, or a clean install driven only from the documentation. None was found by reading the code
or by unit tests, which is the most useful thing this record says about where to spend effort.
All of them now have regression cover in `tests/test_contained*.py`.

---

## Still outstanding

**1. The runtime image is not published.** `ghcr.io/akashgit/remote-factory/factory-runtime:latest`
returns 403. Every new user's `factory contained setup` fails at the pull. The CI workflow that
publishes it (`.github/workflows/runtime-image.yml`) exists but has never run. Until it does, users
must build locally and set `FACTORY_CONTAINED_IMAGE`. **This is the single biggest barrier to anyone
using the feature.**

**2. The cluster half has not been re-verified since the fixes.** Phases 3 and 4 were verified
end-to-end against OpenShift 4.21 *before* the review fixes landed. Since then the k8s code changed:
namespace threading through `attach`/`rm`/`sync`, `bundle` implying the cluster target, `setup`
re-ordering, and the `ls` unconfigured/failed split. Unit tests cover the changes and the manifests
are asserted, but **no pod has run since**. Do an `oc login` and repeat the design's §4.7 before
trusting it.

**3. One verification step was never run.** An agent inside a cluster pod listing its own cluster
tools needs a credentials Secret in the namespace. Creating one is the operator's decision, not the
implementation's. Everything else about that surface is verified — the `.mcp.json` is written, `oc`
is absent from the image, and the `start_build` server answers a real MCP handshake from inside the
pod. Only *a model reading the tool list* is unproven.

**4. The Vertex warnings are duplicated.** A Vertex run with no `--model` prints two warnings that
say nearly the same thing — one from the credential shape, one from the model check. Cosmetic, but
it is exactly the "warnings people learn to ignore" problem fixed elsewhere.

---

## How to reproduce the test environment

Most of these were only findable from a genuinely clean install. To make one:

```bash
CR=~/.factory-cleanroom                     # must be under $HOME: podman does not share /tmp on macOS
mkdir -p $CR/fakehome $CR/project
python3 -m venv $CR/venv
$CR/venv/bin/pip install -e /path/to/remote-factory
cp -R ~/code/rta $CR/project/rta            # or any small project
mkdir -p $CR/fakehome/.config
# podman keeps working; the factory sees a virgin home
ln -sfn ~/.config/containers $CR/fakehome/.config/containers

cat > $CR/ENTER.sh <<'EOF'
#!/bin/bash
CR="$(cd "$(dirname "$0")" && pwd)"
export HOME="$CR/fakehome"; export PATH="$CR/venv/bin:$PATH"
unset FACTORY_CONTAINED_HOME FACTORY_CONTAINED_DRY_RUN ANTHROPIC_API_KEY CLAUDE_CODE_USE_VERTEX
exec "$@"
EOF
chmod +x $CR/ENTER.sh
$CR/ENTER.sh factory contained verify
```

Two traps in the clean room itself:

- **`HOME` must be under the real `$HOME`.** The podman machine shares your home directory and
  nothing else, so a clean room under `/tmp` makes every mount silently empty — which looks like a
  product defect and is not.
- **Do not link `~/.kube` in** unless you mean to. Without it nothing can touch a cluster, which is
  usually what you want when testing the local target.

---

## Environment defects

Facts about podman, OpenShift and the runtime image. Each one presents as something other than what
it is, which is why they are worth writing down.

### The workspace and the host

**A read-only mount of the source `.git` breaks the CEO's own worktrees.** The design said mount it
read-only. But the CEO creates experiment worktrees inside the copy, and `git worktree add` writes a
ref lock into the *common* directory. First cycle died on `cannot lock ref: Read-only file system`,
which reads as a git bug. Now mounted read-write — and the cost is stated: the container can write
the source repo's `.git`. `factory/cli/contained.py`.

**A cluster workspace cannot be a git worktree.** Its `.git` is a *file* pointing at a host path no
pod has, so `git status` fails and state detection reports `no_repo`. The cluster copy is
self-contained (`plan_workspace(self_contained=True)`). `factory/contained/workspace.py`.

**A copy deleted by hand leaves git believing a worktree is still checked out there**, and every
later run of that name fails on "cannot force update the branch … used by worktree at", naming a
directory that no longer exists. `materialize` prunes first.

### Identity and the container

**Rootful podman on macOS: the mount reports as owned by `501:20` inside a container**, not by the
image's UID. The rule differs between rootless, rootful and macOS, so the runtime **probes** — a
throwaway container reports the owner as the kernel inside sees it, and the run matches with
`--user <uid>:0`. `factory/contained/identity.py`.

**A PVC mounts root-owned**, so a pod running as an arbitrary UID cannot write it. The unpack died
on `tar: Cannot mkdir: Permission denied` for a directory the pod could see. Fixed with an `fsGroup`
read from the namespace's `openshift.io/sa.scc.supplemental-groups` range — hardcoding one fails
admission under a `MustRunAs` SCC. `factory/contained/k8s.py`.

**tmux is in neither the UBI repositories nor EPEL.** EPEL never duplicates a package RHEL ships,
and UBI's subset omits it. It is compiled in a builder stage; `configure` hard-requires `yacc`,
which no UBI repo has, so a stub plus `touch cmd-parse.c` is what makes the build work.
`containers/factory/Containerfile`.

### The cluster

**`oc auth can-i --as` disagrees with the API.** Measured on OpenShift 4.21:

| asked | SubjectAccessReview | `oc auth can-i --as` |
|---|---|---|
| `create pods` | true | yes |
| **`create pods/exec`** | **false** | **yes** |
| `get pods/log` | true | yes |
| `create secrets` | false | no |

The CLI collapses a subresource onto its parent when impersonating. That one wrong answer made
`no_pods_exec` — the check standing between the cluster division's sidecar and an agent that can
exec into it — report the boundary broken on *every* cluster. Now uses the API object.
`factory/contained/k8s.py:render_access_review`.

**A SubjectAccessReview without an `apiGroup` asks about the core group.** A review for `builds`
with no group asks about a core resource that does not exist and comes back denied, reporting a
correctly configured division namespace as missing its permissions.

**The unpack marker was shared across runs.** The PVC outlives the run that filled it, so the *next*
run found the marker, skipped its own upload, and would have executed against the previous run's
files. Now per-run (`unpack_marker(run_name)`).

**`oc start-build --follow` exits 0 for a build that failed.** A build that died on a missing
Dockerfile was reported to the agent as "Build succeeded" — and the agent would then validate an
image that was never produced. The verdict now comes from the Build's `.status.phase`.

**…and reading that phase immediately reports every *successful* build as `Running`.** The log
stream closes before the controller writes the final phase. Now polled until terminal.

**Binary builds silently ignore `--build-arg`**, so `--build-arg DOCKERFILE=` did nothing and the
build looked for a file named `Dockerfile`. `dockerfilePath` is patched onto the BuildConfig
instead — which needs `patch` on `buildconfigs` in the Role (not a widening: `create` + `delete`
already give the same power).

**The build sidecar was running the runtime image**, which deliberately has no `oc`. First build:
`oc: command not found`. It runs its own `oc` image now
(`FACTORY_CONTAINED_SIDECAR_IMAGE`), and parses with `sed` because that image has neither jq nor
python.

**A probe pod name ending in a hyphen fails RFC 1123**, reported by the API server as an invalid
*value* rather than as a naming mistake. Names are hashed, not truncated.

**The design's §4.0 check 6 was never implemented** — inference reachability probed from *inside*
the cluster. A host-side check proves nothing about a namespace's egress. Added.

### The local division

**`podman-mcp-server` exits on stdin EOF even in HTTP mode.** A background spawn leaves nothing
listening and writes no error. Started as `tail -f /dev/null | podman-mcp-server --port 8430`.

**`npx` downloads the package on a cold first run**, so a reachability probe issued immediately
concluded the host was unreachable and tore down a division that was seconds from working. The port
is waited for first, and a slow start is reported differently from a routing fault.

**The server has to outlive the command that started it.** The launch returns as soon as the tmux
session exists, while the run continues for hours; a server tied to the launcher was gone before the
agent's first build. Detached into its own process group, PGID recorded, stopped by `rm`.

**A fresh `~/.claude` blocks an interactive run behind three dialogs** — folder trust, project MCP
approval, and Bypass Permissions acceptance. All three are interactive-only (`-p` skips them), so
headless agents never hit them and the interactive CEO always does. It reads as a hang, after the
tokens it took to get there are spent. Pre-answered in `factory/contained/claude_state.py`. One of
the three cannot be answered per-project — the CEO works in a worktree whose name carries a per-run
id — so MCP approval is given as `enableAllProjectMcpServers`.

---

## Command-surface defects

Found by driving every documented command from a clean install:

### Broken behaviour

| | Defect | Fix |
|---|---|---|
| D1 | **`--namespace` was parsed, scope-checked, then dropped.** `attach`/`rm`/`sync` took no namespace at all, so each answered "no namespace given" to someone who had just given one. Every documented cluster lifecycle command was unusable. | Threaded through `dispatch_lifecycle`; the message no longer blames the flag you used |
| D2 | **The run's name printed last**, under ~60 lines of `argv=[…]` including a 45-line embedded Python program — while the docs promised it was printed first | Printed before provisioning; internal events moved to debug |
| D3 | **`setup` dead-ended**: the image 403s, the offered build command needs a repo checkout a pip-installed user does not have, and the footer said to run `setup` — the command that just failed | Pull failure explains both routes; footers never point at themselves |
| D4 | **`bundle` printed a command the CLI rejects** (`bundle --namespace X` — flag after subcommand), and `--namespace` was rejected unless you also passed `--target k8s` | `bundle` implies the cluster target; every generated command parses |
| D5 | **A failed launch left a git worktree and a branch in your repository**, one per attempt, with nothing listing or removing them — while the guide promised "nothing is left behind" | Launches that never start roll back; `rm` prints the two cleanup commands |
| D6 | **Dry-run hid the division** — the one thing worth previewing | Banner and server command shown, marked not started |
| D7 | **`ls` said "Nothing could be listed" when nothing was wrong**, and exited 0 when the engine was down | Three distinct outcomes; non-zero only on real failure |
| D8 | **`verify`'s footer offered `setup` for checks setup cannot fix**, and named the local command on the cluster path | Target-aware and check-aware |
| D9 | **The division binds every interface**, which neither the banner nor the docs said | Stated plainly, with a mitigation. Loopback is not available — see [Decisions](#decisions-that-look-like-defects) |
| D22 | **A malformed `--env` was reported after the workspace copy and a container probe**, late enough for an unrelated failure to mask it | Arguments validated before anything is created |

### Output written for the authors

| | Defect | Fix |
|---|---|---|
| D10 | **Four spec-section citations in user-facing output** — `--help`, the division banner, `--live`, and the guide — pointing at a document shipped in neither the docs site nor the wheel | Every `§`/`spec` reference removed from runtime output and user docs |
| D11 | **`--help` argued with the design**: "share a command surface and an image, not a threat model", "Local has no egress control", unintroduced `SCC` — before the reader knew what the targets were *for*. It also documented no subcommand, omitted `--yes`, and omitted every environment variable | Rewritten to orient: what it does, what each target is for, the subcommands, the environment, and one sentence on the limitation |
| D12 | **Provenance hints explained the design rather than the fix** — two-thirds of each message described the bug that would have happened, mentioning `no_repo`, "the CEO", "state detection" | Cause, then `Try:`, then optionally why |
| D13 | **Internal event names printed at info level**, one with a field called `token=` next to a filesystem path | All `contained_*` events to debug; `token=` → `argument=` |
| D14 | **Three warnings on every run**, one irrelevant to most payloads, one crying wolf | Growth warning only for scoring payloads; macOS check reads the real mount list; ordering fixed |
| D15 | **`setup`'s prompt printed a bare `Error:` on EOF**, and the guide never mentioned the prompt | Defaults to local; documented |
| D16 | **The guide's transcripts were tidied rather than captured** | Re-captured from a clean install |

### Rough edges

D17 `rm` echoed podman's copy of the name · D18 a mistyped subcommand produced a message about
materializing workspaces · D19 `--yes` was an undocumented exception to a rule stated absolutely ·
D20 `bundle` invented a namespace called `factory` · D21 `k8s setup` said "About to apply … with
your own credentials" when there were none, and contradicted itself in adjacent lines.

---

## Defects found in use

**U1 — `ls` reached for a cluster the user never set up.** Someone who answers "local" at setup, and
has a kubeconfig entry pointing at an unreachable cluster, waited on a multi-second i/o timeout on
every `ls` and was then told about a target they never asked for. The earlier fix only checked
whether a *context existed* — it does — so it dialled anyway.

Now the cluster is consulted only when there is reason to: it was set up, something has run on it,
or `--target k8s` asks now. Targets are recorded in `~/.factory-contained/targets.json`
(`factory/contained/usage.py`). Listing also carries a client-side deadline, because kubectl retries
internally and would otherwise block for minutes. **11s → 0.4s.**

**U2 — one `exit` destroyed the run permanently.** Exiting the shell inside tmux closed the last
pane → the window → the session, taking the scrollback. The container stayed up, so `ls` still said
`running` while `attach` answered `no sessions` with no way back. Three changes, all needed:

- `remain-on-exit` — the pane dies, the session and its output survive.
- a `pane-died` hook that detaches the client — **without this the first change makes it worse**:
  you would be stuck in a dead pane that accepts no input, escapable only if you know the tmux
  detach key.
- `attach` respawns a dead pane into a shell before attaching, so you land somewhere you can type.

Plus: `ls` now reports what the *run* is doing (the container's PID 1 outlives it by design, so
"running" was never a claim about the run), and `attach` on a finished run says so and offers a
shell, `sync` and `rm`.

**U3 — an orphaned division endpoint would be silently adopted.** Removing a container with
`podman rm` instead of
`factory contained rm` — or deleting `~/.factory-contained` — orphans the server with no PID file.
The ownership check consulted only the factory's own records, so the next `--division` run found no
recorded owner, saw the port answering, and would have handed the agent someone else's server. It
now asks the port as well as the records.

---

## Decisions that look like defects

Do not "fix" these without reading why.

**The division binds `0.0.0.0`, not loopback.** `podman-mcp-server` has no bind flag, and the
container reaches the host through a gateway address rather than through localhost — so a loopback
bind would make the build tools *unreachable*, not safer. Disclosure is the mitigation.

**The container outlives the run.** Nothing is auto-reaped, because a failed run is exactly when its
state is worth reading. `ls` distinguishes the two.

**The workspace copy and its branch survive `rm`.** They hold the run's output. `rm` prints the
commands that remove them when you are done.

**Dry-run's `[run]` line is ~45 lines and is not trimmed.** Its contract is to print the same
commands the real path runs; a tidier rendering could drift from what executes.

**The source repository's `.git` is mounted read-write.** Required for the CEO's own experiment
worktrees. "The host tree is untouched" remains true — that is about the *working* tree.

**`--yes` is accepted after a subcommand while other flags are not.** `rm <name> --yes` is the order
people type. Documented as the exception.

**`factory/podman.py` and `factory/contained/k8s.py` compose commands and never execute them.** That
split is what makes `FACTORY_CONTAINED_DRY_RUN=1` print the same argv the real path runs.

---

## Where things live

| Concern | Module |
|---|---|
| podman CLI composition | `factory/podman.py` |
| cluster CLI composition and manifests | `factory/contained/k8s.py` |
| workspace copy, rollback, cleanup hints | `factory/contained/workspace.py` |
| provenance assertions | `factory/contained/provenance.py` |
| container identity probe | `factory/contained/identity.py` |
| credential shape, never material | `factory/contained/credentials.py` |
| what crosses the boundary | `factory/contained/env.py` |
| local division | `factory/contained/division.py` |
| cluster division, sidecar, `start_build` | `factory/contained/k8s_division.py` |
| prerequisite checks | `factory/contained/prereq.py`, `factory/contained/k8s_setup.py` |
| which targets this machine uses | `factory/contained/usage.py` |
| Claude Code first-run answers | `factory/contained/claude_state.py` |
| secret scan | `factory/contained/secrets.py` |
| CLI | `factory/cli/contained.py`, `factory/cli/contained_k8s.py` |

Regression cover: `tests/test_contained.py`, `test_contained_workspace.py`,
`test_contained_prereq.py`, `test_contained_division.py`, `test_contained_k8s.py`,
`test_contained_k8s_division.py`.

**The suite has ~62 pre-existing failures unrelated to this feature** (workflow, spec and tmux
suites). They reproduce with these changes stashed and on `main`. Compare against a baseline before
assuming you broke something.
