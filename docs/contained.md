# Contained Runtimes

`factory contained` runs any factory command somewhere other than your shell — in a podman container
on your machine, or in a pod on an OpenShift cluster.

```bash
factory contained -- ceo ~/code/my-project
```

Two things make it worth using. The run happens against a **pinned toolchain** — a known Python, a
known set of agent CLIs, a known set of build tools — rather than whatever your machine has
accumulated. And it works on a **copy** of your project, so your working tree is untouched and
nothing is left behind when the runtime is removed.

Everything after `--` is handed inward **verbatim**. The runtime is a place to run the factory, not
a mode of it, so the host never parses what you pass and cannot break when the CLI grows.

!!! warning "Read the guarantees before trusting them"
    `contained` bounds *accidents* and gives runs a reproducible environment. It does **not** confine
    agent-authored code, it is **not** a multi-tenant boundary, and it does **not** replace review.
    See [What each target actually guarantees](#what-each-target-actually-guarantees).

---

## Quick start

```bash
factory contained setup                  # pull the image, check prerequisites
factory contained verify                 # report what's missing, with the fix for each
factory contained -- ceo ~/code/my-project
factory contained ls                     # what's running
factory contained attach <name>          # watch it; Ctrl-b d detaches, the run continues
factory contained sync <name>            # how to get the work back
factory contained rm <name>              # tear it down
```

You need `podman` (with its machine running on macOS), and inference credentials — an
`ANTHROPIC_API_KEY`, a Vertex configuration, or a credential profile in `~/.factory/config.toml`.

---

## What each target actually guarantees

The two targets share a command surface and an image, **not a threat model**.

| | `--target local` | `--target k8s` |
|---|---|---|
| Runtime | podman container on your machine | plain pod on Kubernetes/OpenShift |
| Syscall confinement | podman's default seccomp profile only | the container runtime's default only |
| Filesystem | project is a **copy**, bind-mounted read-write; your *working* tree untouched — but the source repo's `.git` is writable | restricted SCC; workspace is a **copy** on a PVC |
| Identity | non-root, matched to the mount's owner | restricted SCC, arbitrary namespace UID |
| Egress | **none** — full network access | NetworkPolicy only |
| Credentials | **inside the container** | a Secret in the namespace, mounted into the pod |
| Division | full host podman engine | OpenShift `Build` + validation pods |

Four honest admissions:

1. **Local is the weaker runtime.** It has no egress control and holds credentials directly.
2. **The division is a hole by design.** Builds cannot happen inside either boundary, so both
   divisions reach outward. Opt-in and separately named is the mitigation for the decision.
3. **Neither replaces review.** A contained run does not make its diff trustworthy.
4. **Neither is a multi-tenant boundary.** Both assume you own the machine or the namespace, and
   that the code under improvement is yours.

---

## Command reference

```
factory contained [runtime flags] -- <any factory command>
factory contained {ls|attach|rm|sync|setup|verify|bundle} [name]
```

**Both targets**

| Flag | Default | Meaning |
|---|---|---|
| `--target local\|k8s` | `local` | Which runtime |
| `--division` | off | Enable the container-manufacturing plane for that target |
| `--name NAME` | derived | Runtime name |
| `--env KEY=VALUE` | — | Extra environment, repeatable |
| `--forward VAR` | — | Forward a named host variable, repeatable |
| `--image REF` | published default | Override the runtime image |
| `--yes` | off | Skip confirmations (`rm` of an active run, the secret-scan gate) |

**Local only**

| Flag | Meaning |
|---|---|
| `--mount PATH` | Additional host path bind-mounted in, repeatable |
| `--live` | Reserved; not implemented |

**K8s only**

| Flag | Default | Meaning |
|---|---|---|
| `--namespace NS` | current context | Never hardcoded |
| `--storage-class SC` | cluster default | Workspace PVC |

A flag used against the wrong target fails at parse time naming the target it belongs to — never
silently ignored. Runtime flags go **before** the subcommand; anything flag-shaped after it is an
error rather than a name.

---

## Interaction examples

Transcripts below are from real runs against
[`beatsmonster/rta`](https://github.com/beatsmonster/rta), with `$HOME` shortened. They were
captured separately, so run names and ages differ between them.

### Checking prerequisites

`verify` reports; it changes nothing. Every failure carries the command that fixes it.

```console
$ factory contained verify
[ok  ] container_engine: podman reachable (5.7.1, rootful)
[ok  ] runtime_image: ghcr.io/akashgit/remote-factory/factory-runtime:latest present locally
[FAIL] inference: no inference configuration found: CLAUDE_CODE_USE_VERTEX is unset,
       ANTHROPIC_API_KEY is unset, and ~/.factory/config.toml defines no credential profiles
         fix: export ANTHROPIC_API_KEY=... and re-run with --forward ANTHROPIC_API_KEY, or
              configure Vertex (CLAUDE_CODE_USE_VERTEX=1 CLOUD_ML_REGION=...
              ANTHROPIC_VERTEX_PROJECT_ID=... plus `gcloud auth application-default login`), or
              add a [credentials.<name>] section to ~/.factory/config.toml

1 check(s) failed. Fix them, or run `factory contained setup`.
```

Inference is always reported by **shape** — which backend, which model, which variable or file
supplied it — and never by printing material:

```console
$ factory contained setup
The podman engine is not reachable. Starting the podman machine...
Runtime image already present: ghcr.io/akashgit/remote-factory/factory-runtime:latest
[ok  ] container_engine: podman reachable (5.7.1, rootful)
[ok  ] runtime_image: ghcr.io/akashgit/remote-factory/factory-runtime:latest present locally
[ok  ] inference: Vertex, project my-project in us-east5, model <unset — pass --model in the
       payload>, credential from Application Default Credentials at ~/.config/gcloud

All checks passed. Start a run with `factory contained -- ceo <path>`.
```

`setup` is idempotent — re-running changes nothing that is already correct, and it is the supported
way to repair a partial setup.

### Starting a run

The runtime's identifier is printed **first**, before any long-running work. A run whose name you
cannot see is a run you cannot manage.

```console
$ factory contained -- ceo ~/code/rta
Warning: Growth context not configured: FACTORY_MANAGED_DIRS, FACTORY_VAULT_PATH are unset.
Growth dimensions merge 50/50 into the composite score, so eval scores computed in this container
are NOT comparable to host scores. Continuing anyway.
rta-8ac57c
  attach:  factory contained attach rta-8ac57c
  result:  factory contained sync rta-8ac57c
```

The command returns as soon as the run is going; the run itself continues in tmux inside the
container.

### Watching, detaching, coming back

```console
$ factory contained ls
NAME                              TARGET  PROJECT       AGE   STATE
rta-8ac57c                        local   8ac57cfe4ab6  1m    running

$ factory contained attach rta-8ac57c
```

That drops you into the live session. `Ctrl-b d` detaches and **leaves the run going** — the tmux
prefix, because the run lives in tmux precisely so that detaching is safe.

### Getting the work back

Nothing is ever merged for you.

```console
$ factory contained sync rta-8ac57c
rta-8ac57c: the workspace is already on this machine — a bind mount, not a transfer.
Work is on branch contained/rta-8ac57c in ~/.factory-contained/rta-8ac57c/rta.
  Review:  git -C ~/.factory-contained/rta-8ac57c/rta status && git -C ... diff
  Merge:   git -C ~/code/rta merge contained/rta-8ac57c
```

### Tearing down

```console
$ factory contained rm rta-8ac57c
rta-8ac57c
rta-8ac57c: deleted. Workspace copy remains at ~/.factory-contained/rta-8ac57c/rta.
Work is on branch contained/rta-8ac57c in ~/.factory-contained/rta-8ac57c/rta.
  Review:  git -C ~/.factory-contained/rta-8ac57c/rta status && git -C ... diff
  Merge:   git -C ~/code/rta merge contained/rta-8ac57c
```

The container **persists** until you remove it. Nothing is auto-reaped, because a failed run is
exactly when its state is worth reading.

### When the workspace is wrong

Five assertions run between provisioning and the first agent call. A failure aborts **before** any
tokens are spent, names the likely cause, and leaves the runtime up so you can look:

```console
$ factory contained -- ceo ~/code/rta
contained: step 'assert:git_usable' failed
  git is not usable in the workspace. State detection then reports no_repo, the CEO silently drops
  to build mode, and the eventual error names a flag several steps away from the cause. For a git
  worktree this usually means the source repository's git directory is not mounted — a worktree's
  .git is a *file* pointing at it.
  The container is still there for inspection:
    podman exec -it rta-8ac57c sh
    factory contained rm rta-8ac57c
```

### Composing without provisioning

`FACTORY_CONTAINED_DRY_RUN=1` prints the exact commands the real path would run, and provisions
nothing:

```console
$ FACTORY_CONTAINED_DRY_RUN=1 factory contained -- study ~/code/rta
DRY RUN — rta-8ac57c (ghcr.io/…/factory-runtime:latest); nothing is provisioned.
[create] podman run -d --init --name rta-8ac57c --label factory.contained=true …
[assert:project_present] podman exec rta-8ac57c sh -lc '[ -d "…" ] && [ -n "$(ls -A "…")" ]'
[assert:git_usable] podman exec rta-8ac57c sh -lc 'git -C "…" status --porcelain >/dev/null 2>&1'
[assert:factory_state] podman exec rta-8ac57c test -f …/.factory/config.json
[assert:writable] podman exec rta-8ac57c sh -lc 'touch "…/.factory-write-probe" && rm -f …'
[assert:content_hash] podman exec rta-8ac57c sh -lc 'sha256sum "…" | grep -q "^<digest> "'
[run] podman exec rta-8ac57c sh -lc 'tmux new-session -d -s factory -c … '
```

Which assertions appear depends on what your project actually has: `factory_state` only when the
host has a `.factory/config.json`, `git_usable` only when it is a git repository. Asserting
unconditionally would blame a transfer fault for a project that was simply never initialized.

The `[run]` line is long — it carries the Claude Code state seeding described above, verbatim,
because dry-run's contract is to print *the same argv the real path runs* rather than a tidier
rendering that could drift from it.

Secret-looking values are redacted anywhere a command is printed:

```console
$ FACTORY_CONTAINED_DRY_RUN=1 factory contained --forward GH_TOKEN -- study ~/code/rta
… --env GH_TOKEN=<redacted> …
```

---

## The local division

`--division` gives the contained agent your **host's** podman engine, so it can build an image, run
it, read the failure and iterate.

Builds happen outside the container because the container has no engine of its own, and giving it
one means nested containerization — a privileged container or a user-namespace setup that is fragile
on Linux and unavailable inside the macOS podman machine. So the division reaches outward, and it is
opt-in and separately named for exactly that reason.

```console
$ factory contained --division -- ceo ~/code/rta --focus "add a Containerfile"

  ┌─ DIVISION ENABLED ─────────────────────────────────────────────────────────────
  │ podman-mcp-server is listening on port 8430 with NO AUTHENTICATION.
  │ The container reaches it at http://host.containers.internal:8430/mcp.
  │ Anything that can reach that port can build and run containers on this host —
  │ outside the container boundary, by necessity.
  │
  │ It outlives this command, because the run does. Stop it with:
  │     factory contained rm buildcycle
  └────────────────────────────────────────────────────────────────────────────────

buildcycle
  attach:  factory contained attach buildcycle
  result:  factory contained sync buildcycle
```

The endpoint lives as long as the run, not as long as the launching command — the launch returns
immediately while the run continues for minutes or hours. `factory contained rm` stops it.

The agent gets the podman tool surface plus a brief telling it these are capabilities it already
has. Asked to name its tools, it answers with them rather than proposing to build a CLI wrapper:

```console
$ factory contained --division -- agent builder \
    --task "List the container tools available to you. Do not write code." --project ~/code/rta

I have access to the following Podman/Docker container management tools:
**Container Operations:**
- `mcp__podman__container_list` — List running containers
- `mcp__podman__container_run` — Run a container from an image
- `mcp__podman__container_logs` — Display container logs
…
**Image Operations:**
- `mcp__podman__image_build` — Build an image from a Dockerfile/Containerfile
…
```

Without the flag, nothing is started, no `.mcp.json` is written, and the agent has no container
tools. The division is genuinely opt-in.

Requires `npx` on `PATH`.

---

## The cluster target

`--target k8s` runs the factory unattended on hardware your laptop is not: real CPU, real memory,
amd64, and a workspace that survives the pod.

### One-time namespace setup

`bundle` prints plain namespace-scoped YAML and never applies it. `setup` prints it, asks, and
applies it **with your own credentials**:

```console
$ factory contained --target k8s --namespace factory-contained setup
About to apply the following to namespace factory-contained with your own oc credentials:

# factory contained — namespace prerequisites for factory-contained
…
apiVersion: v1
kind: ServiceAccount
…

Apply it? [y/N] y
serviceaccount/factory created
role.rbac.authorization.k8s.io/factory-runtime created
rolebinding.rbac.authorization.k8s.io/factory-runtime created
rolebinding.rbac.authorization.k8s.io/factory-scc created
persistentvolumeclaim/factory-workspace created

The credentials Secret is yours to create — the factory never handles the material:
  oc create secret generic factory-credentials -n factory-contained \
      --from-literal=ANTHROPIC_API_KEY=...
```

Then `verify` checks every object, every verb the ServiceAccount needs, the Secret's **keys** (never
its values), and that inference is reachable from a pod *inside* the namespace:

```console
$ factory contained --target k8s --namespace factory-contained verify
[ok  ] cluster_cli: oc, context factory-contained/api-…:443/you@example.com
[ok  ] namespace: factory-contained exists and is accessible
[ok  ] bundle:serviceaccount/factory: serviceaccount/factory present
…
[ok  ] permissions: serviceaccount/factory has every verb the run needs
[ok  ] no_pods_exec: serviceaccount/factory cannot exec into pods, which is what makes the build
       sidecar a boundary
[ok  ] credentials_secret: secret/factory-credentials carries the Anthropic API key
[ok  ] inference_from_cluster: a pod in this namespace reached the configured inference backend
[ok  ] secret_scanner: gitleaks present; workspaces are scanned before they leave this machine

All checks passed. Start a run with `factory contained --target k8s --namespace factory-contained -- ceo <path>`.
```

Before setup, the same command lists what is missing with the command that restores each — e.g.
`factory contained bundle --namespace factory-contained | oc apply -f -`.

### Running

```console
$ factory contained --target k8s --namespace factory-contained -- run ~/code/rta --loop
k8srun
  attach:  factory contained --target k8s attach k8srun
  result:  factory contained --target k8s sync k8srun
  logs:    oc logs -f k8srun -n factory-contained -c factory
```

The workspace is packed into one tarball, streamed into an initContainer that is waiting for it, and
unpacked onto the PVC before the factory container starts. `oc cp` of a directory is one API round
trip per file, which is painfully slow on a repository.

### The secret scan

Nothing leaves your machine unscanned. Gitleaks runs over the workspace before the upload:

```console
$ factory contained --target k8s -- study ~/code/rta
gitleaks: 1 finding(s)
  .env:1  [github-pat] Uncovered a GitHub Personal Access Token, potentially leading to
          unauthorized repository access and sensitive content exposure.

This workspace is about to be copied onto cluster storage. Anything above goes with it.
Refusing to upload without confirmation. Re-run with --yes to proceed non-interactively.
```

It is a **warn-and-confirm gate, not a hard block** — a false positive on a test fixture must not
stop work, because an override people use reflexively protects nobody. `--yes` proceeds, and says so
rather than passing silently. If gitleaks is not installed, the upload warns that it is unscanned
rather than quietly going ahead.

### Getting the work back, and tearing down

```console
$ factory contained --target k8s sync k8srun
k8srun: workspace fetched to ~/.factory-contained/k8srun/workspace.tar.gz.
  Review:  tar tzf ~/.factory-contained/k8srun/workspace.tar.gz
  Unpack:  mkdir -p <dir> && tar xzf ~/.factory-contained/k8srun/workspace.tar.gz -C <dir>
Nothing is merged automatically.

$ factory contained --target k8s rm k8srun
k8srun: pod deleted.
  The workspace is still on PVC factory-workspace in factory-contained. Fetch it with
  `factory contained --target k8s sync k8srun` before deleting the claim.
```

The PVC is deliberately left alone: it may hold the only copy of a long run's work.

### The cluster division

`--target k8s --division` is **OpenShift only**, refused at launch by API presence rather than by
whether `oc` happens to be installed. Builds go through OpenShift `Build` objects, submitted by a
**sidecar container** that is the only holder of `oc` and the ServiceAccount token — the agent's
container has neither, and cannot exec into the sidecar because the Role excludes `pods/exec`.
`verify` asserts that verb's absence; it is the one check that fails when something *succeeds*.

The agent gets one tool for building — `start_build(dockerfile, tag)` — plus namespace-scoped
cluster tools for launching validation pods and reading logs.

---

## Things that fail quietly, and what the runtime does about them

| Trap | What it looks like | What the runtime does |
|---|---|---|
| A container UID that does not own the mount | Agent edits vanish with no error | Probes a throwaway container for the mount's owner and matches the run to it |
| A run that starts on the wrong files | A plausible-looking result from stale code | Five provenance assertions before the first agent call |
| A `HEAD` checkout instead of your tree | Uncommitted work silently absent | The copy is a worktree with your working tree synced over it — `.factory/` included |
| Claude Code's first-run dialogs | The run hangs at a menu nobody is watching | Trust, MCP approval and bypass-permissions answers are pre-recorded |
| A root-owned PVC | `tar: Cannot mkdir: Permission denied` | `fsGroup` read from the namespace's allocated range |
| A reused PVC | The next run executes against the previous run's files | The unpack marker is per-run |
| Growth context missing | In-container eval scores silently incomparable | Loud warning at launch; never fails the run |

---

## Environment

| Variable | Purpose |
|---|---|
| `FACTORY_CONTAINED_IMAGE` | Override the runtime image |
| `FACTORY_CONTAINED_SIDECAR_IMAGE` | Override the k8s build sidecar's `oc` image |
| `FACTORY_CONTAINED_HOME` | Where workspace copies live (default `~/.factory-contained`) |
| `FACTORY_CONTAINED_DRY_RUN` | Compose commands, provision nothing |

What crosses into the runtime is `FACTORY_` by default, plus exactly what `--forward` names, plus
the backend variables the resolved credential shape requires. **Nothing implicit.** `~/.factory/` is
mounted read-write so config, profiles, the registry and evolved playbooks work as on the host;
`~/.claude/projects/`, `FACTORY_MANAGED_DIRS`, `FACTORY_VAULT_PATH` and `GH_TOKEN` are opt-in via
`--mount` / `--forward`.

---

## Troubleshooting

**"podman is installed but its engine is not reachable"** — on macOS the machine stops quietly.
`podman machine start`, or `factory contained setup`, which does it for you.

**"the workspace is not writable by the runtime identity"** — the mount's owner and the container
UID disagree. The message names the mount path, the UID and the owner.

**macOS: the workspace is empty inside** — the podman machine shares `$HOME` by default; a path
outside it is not mounted at all rather than mounted empty. The launch warns when a mount source is
outside `$HOME`.

**"container 'x' already exists"** — a previous run left it. Attach to it, `rm` it, or pass `--name`.
A container that is no longer running is reaped automatically and the run retried once.

**"is already running a session — this is the same run, not a new one"** (k8s) — the pod is mid-run.
Attach, or `rm` and start again.

**"the division port 8430 is already held by the run 'x'"** — one port, one server. Finish or remove
that run first, or run this one without `--division`.

**Vertex 429s on every call** — pass an explicit `--model`. `MAX_THINKING_TOKENS=0` is pinned for you.

---

## Implementation

| Concern | Module |
|---|---|
| All podman CLI knowledge | `factory/podman.py` |
| All cluster CLI knowledge | `factory/contained/k8s.py` |
| Workspace copy | `factory/contained/workspace.py` |
| Provenance assertions | `factory/contained/provenance.py` |
| Container identity probe | `factory/contained/identity.py` |
| Credential shape | `factory/contained/credentials.py` |
| Local division | `factory/contained/division.py` |
| Cluster division | `factory/contained/k8s_division.py` |
| Prereq bundle | `factory/contained/bundle.py` |
| Secret scan | `factory/contained/secrets.py` |
| CLI | `factory/cli/contained.py`, `factory/cli/contained_k8s.py` |

Both CLI modules **compose** commands and do not execute them, which is what makes
`FACTORY_CONTAINED_DRY_RUN=1` print the same argv the real path runs rather than a separate
rendering that drifts.

The runtime image is `containers/factory/Containerfile` — UBI9 plus the factory wheel, the agent
CLIs, and tmux — published multi-arch by `.github/workflows/runtime-image.yml`. `factory contained
setup` pulls it; it does not build.
