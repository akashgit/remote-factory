# Contained Runtimes

`factory contained` runs any factory command somewhere other than your shell — in a podman container
on your machine, or in a pod on an OpenShift cluster.

```bash
factory contained -- ceo ~/code/my-project
```

Two things make it worth using. The run happens against a **pinned toolchain** — a known Python, a
known set of agent CLIs, a known set of build tools — rather than whatever your machine has
accumulated. And it works on a **copy** of your project, so your working tree is never modified.

The copy is a git worktree of your repository, which means two things survive a run on purpose: the
copy itself, holding whatever the run produced, and a `contained/<name>` branch pointing at it.
`rm` prints the two commands that remove both once you are done with them.

Everything after `--` is handed inward **verbatim**. The runtime is a place to run the factory, not
a mode of it, so the host never parses what you pass and cannot break when the CLI grows.

!!! warning "Read the guarantees before trusting them"
    `contained` bounds *accidents* and gives runs a reproducible environment. It does **not** confine
    agent-authored code, it is **not** a multi-tenant boundary, and it does **not** replace review.
    See [What it does and does not protect you from](#what-it-does-and-does-not-protect-you-from).

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

## Choosing a target

| | `--target local` | `--target k8s` |
|---|---|---|
| Where it runs | a podman container on your machine | a pod on a Kubernetes/OpenShift cluster |
| Good for | everyday work; attaching and watching | long unattended runs; more CPU and memory than a laptop |
| Needs | podman | a namespace, and a one-time setup you apply yourself |
| Your project | a copy, bind-mounted from disk | a copy, uploaded to a volume that outlives the pod |
| Credentials | taken from your shell, and they enter the container | a Secret you create in the namespace |
| Survives a laptop closing | no | yes |

### What it does and does not protect you from

`contained` exists to make runs **reproducible** and to keep them **off your working tree**. Both
targets do that well.

It is **not a security sandbox**, and it is worth being concrete about what that means:

- The agent's code runs with normal network access and can reach anything your machine can. Nothing
  restricts what it writes or fetches.
- Locally, your inference credentials are inside the container, because the agent needs them to work.
- A contained run does not make its diff safe to merge. Review the result exactly as you would
  review any other change.
- Neither target is built for running code you do not trust, or for sharing a machine or namespace
  with people you do not trust.

The cluster target is the more constrained of the two — it runs under a restricted security context
with namespace-scoped permissions — but the point above still stands for both.

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
[FAIL] container_engine: podman is installed but its engine is not reachable: Cannot connect to Podman...
         fix: podman machine start
[FAIL] runtime_image: ghcr.io/akashgit/remote-factory/factory-runtime:latest is not present locally
         fix: factory contained setup   # pulls ghcr.io/akashgit/remote-factory/factory-runtime:latest
              or, if it is not published yet, point at one you have:
              export FACTORY_CONTAINED_IMAGE=<your-image>
[FAIL] inference: no inference configuration found: CLAUDE_CODE_USE_VERTEX is unset,
       ANTHROPIC_API_KEY is unset, and ~/.factory/config.toml defines no credential profiles
         fix: export ANTHROPIC_API_KEY=... and re-run with --forward ANTHROPIC_API_KEY, or
              configure Vertex (...), or add a [credentials.<name>] section to ~/.factory/config.toml

3 check(s) failed. `factory contained setup` can fix container_engine, runtime_image; the rest
need the fix shown above each one.
```

That is what a first run looks like on a machine with nothing set up. `setup` fixes the first two;
the third is yours, because the factory never handles credential material.

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

!!! note "If the image cannot be pulled"
    The runtime image is published by CI. If the pull fails, `setup` prints two ways forward: point
    `FACTORY_CONTAINED_IMAGE` at an image you already have, or build one from a checkout of the
    repository — the Containerfile ships in git, not in the installed package.

Run without `--target`, and at a terminal, `setup` asks which runtime you are preparing first:

```console
$ factory contained setup
What are you setting up?
  1) local  — a podman container on this machine
  2) k8s    — a pod on a cluster
  3) both
Choice [1]:
```

Pass `--target local` or `--target k8s` to skip the question. `setup` is idempotent — re-running
changes nothing that is already correct, and it is the supported way to repair a partial setup.

### Starting a run

The runtime's identifier is printed **first**, before any long-running work. A run whose name you
cannot see is a run you cannot manage.

```console
$ factory contained --name rta-run -- backlog-list ~/code/rta
Warning: no inference credentials are configured, so every agent call in this run will fail.
  Set one of these before running, and pass it inward:
    export ANTHROPIC_API_KEY=...   then add:  --forward ANTHROPIC_API_KEY
  Run `factory contained verify` to check.
Starting rta-run
  attach:  factory contained attach rta-run
  result:  factory contained sync rta-run
  stop:    factory contained rm rta-run

rta-run is running.
```

That is the whole output. The command returns as soon as the run is going; the run itself continues
in tmux inside the container. Set `FACTORY_LOG_LEVEL=debug` if you want to see every command the
runtime issued.

### Watching, detaching, coming back

```console
$ factory contained ls
NAME                              TARGET  PROJECT       AGE   STATE
rta-run                           local   e06e95065606  1s    running

$ factory contained attach rta-run
```

`ls` covers both targets, but it only asks the cluster once you have actually used one — otherwise
a laptop that has only ever run locally would wait on a network timeout and then be told about a
cluster it never set up. `--target k8s ls` always asks.

That drops you into the live session. `Ctrl-b d` detaches and **leaves the run going** — the tmux
prefix, because the run lives in tmux precisely so that detaching is safe.

Typing `exit` is safe too. It ends the shell inside the session and returns you to your own
terminal; the session and everything it printed stay, and attaching again gives you a fresh shell in
the same window. `ls` shows such a run as `finished` rather than `running`, because the container
deliberately outlives the run inside it.

### Getting the work back

Nothing is ever merged for you.

```console
$ factory contained sync rta-run
rta-run: the workspace is already on this machine — a bind mount, not a transfer.
Work is on branch contained/rta-run in ~/.factory-contained/rta-run/rta.
  Review:  git -C ~/.factory-contained/rta-run/rta status && git -C ... diff
  Merge:   git -C ~/code/rta merge contained/rta-run
```

### Tearing down

```console
$ factory contained rm rta-run
rta-run: deleted. Your work is kept — it is not removed with the runtime.
Work is on branch contained/rta-run in ~/.factory-contained/rta-run/rta.
  Review:  git -C ~/.factory-contained/rta-run/rta status && git -C ... diff
  Merge:   git -C ~/code/rta merge contained/rta-run

This run left a git worktree and a branch in your repository. Remove them with:
  git -C ~/code/rta worktree remove ~/.factory-contained/rta-run/rta
  git -C ~/code/rta branch -D contained/rta-run
```

The container **persists** until you remove it. Nothing is auto-reaped, because a failed run is
exactly when its state is worth reading. A launch that fails *before* the container exists cleans
its own workspace up, so only runs that actually started leave anything behind.

### When the workspace is wrong

Five assertions run between provisioning and the first agent call. A failure aborts **before** any
tokens are spent, names the likely cause, and leaves the runtime up so you can look:

```console
$ factory contained --name rta-run -- ceo ~/code/rta
contained: step 'assert:git_usable' failed
  The workspace is not a usable git repository inside the runtime.
  Most likely the repository this project belongs to was not mounted — a git worktree's .git is a
  file pointing at a directory elsewhere.
  Try:  factory contained --mount <path-to-that-repository> -- <your command>
  The container is still there for inspection:
    podman exec -it rta-run sh
    factory contained rm rta-run

This run left a git worktree and a branch in your repository. Remove them with:
  git -C ~/code/rta worktree remove ~/.factory-contained/rta-run/rta
  git -C ~/code/rta branch -D contained/rta-run
```

Each hint names the likely cause and what to try. The container is left running so you can look
inside it before removing it.

### Composing without provisioning

`FACTORY_CONTAINED_DRY_RUN=1` prints the exact commands the real path would run, and provisions
nothing:

```console
$ FACTORY_CONTAINED_DRY_RUN=1 factory contained -- study ~/code/rta
DRY RUN — rta-run (ghcr.io/…/factory-runtime:latest); nothing is provisioned.
[create] podman run -d --init --name rta-run --label factory.contained=true …
[assert:project_present] podman exec rta-run sh -lc '[ -d "…" ] && [ -n "$(ls -A "…")" ]'
[assert:git_usable] podman exec rta-run sh -lc 'git -C "…" status --porcelain >/dev/null 2>&1'
[assert:factory_state] podman exec rta-run test -f …/.factory/config.json
[assert:writable] podman exec rta-run sh -lc 'touch "…/.factory-write-probe" && rm -f …'
[assert:content_hash] podman exec rta-run sh -lc 'sha256sum "…" | grep -q "^<digest> "'
[run] podman exec rta-run sh -lc 'tmux new-session -d -s factory -c … '
      […the run line is ~45 lines: it embeds the Claude Code state seeding verbatim…]
```

Which assertions appear depends on what your project actually has: `factory_state` only when the
project has a `.factory/config.json`, `git_usable` only when it is a git repository.

The `[run]` line really is that long, and it will look like line noise. Dry-run's contract is to
print *the same commands the real path runs*, so it is not trimmed — a tidier rendering could drift
from what actually executes, which would defeat the point of previewing.

Secret-looking values are redacted anywhere a command is printed:

```console
$ FACTORY_CONTAINED_DRY_RUN=1 factory contained --forward GH_TOKEN -- study ~/code/rta
… --env GH_TOKEN=<redacted> …
```

---

## The local division

`--division` gives the contained agent your **host's** podman engine, so it can build an image, run
it, read the failure and iterate.

Builds happen on your machine rather than inside the container: the container has no container
engine of its own, and nesting one inside it is not workable on macOS. That is why this is a
separate flag rather than something always on.

```console
$ factory contained --division --name buildcycle -- ceo ~/code/rta --focus "add a Containerfile"

  ┌─ Container builds enabled (--division) ───────────────────────────────────────
  │ Started podman-mcp-server so the agent can build and run container images.
  │ The run reaches it at http://host.containers.internal:8430/mcp
  │
  │ It listens on 0.0.0.0:8430 — every network interface, not just this
  │ machine — and it has no authentication. For as long as the run lasts, anyone
  │ who can reach that port can build and run containers as you.
  │
  │ Avoid --division on untrusted networks.
  │ It stops when the run is removed:
  │     factory contained rm buildcycle
  └───────────────────────────────────────────────────────────────────────────────

Starting buildcycle
  attach:  factory contained attach buildcycle
  result:  factory contained sync buildcycle
  stop:    factory contained rm buildcycle

buildcycle is running.
```

The endpoint lives as long as the run, not as long as the launching command — the launch returns
immediately while the run continues for minutes or hours. `factory contained rm` stops it.

It cannot be bound to loopback instead: the container reaches your machine through a gateway
address rather than through localhost, so a loopback bind would make the build tools unreachable
rather than make them safer.

`FACTORY_CONTAINED_DRY_RUN=1` shows the same banner, marked as not started, so you can see what
`--division` would do before doing it.

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
`factory contained --namespace factory-contained bundle | oc apply -f -`.

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

## Checks the runtime runs for you

Before the first agent call, the runtime asserts that the workspace it is about to use is the one
you meant — that it is present and non-empty, that git works in it, that `.factory/` arrived if your
project has one, that it is writable, and that a file's contents match the copy on your machine.

Each of these can fail silently otherwise: a read-only workspace looks like an agent whose edits
keep vanishing, and a stale copy produces a plausible result from the wrong code. A failed check
stops the run before any tokens are spent and leaves the container up so you can look inside it.

---

## Environment

| Variable | Purpose |
|---|---|
| `FACTORY_CONTAINED_IMAGE` | Override the runtime image |
| `FACTORY_CONTAINED_SIDECAR_IMAGE` | Override the k8s build sidecar's `oc` image |
| `FACTORY_CONTAINED_HOME` | Where workspace copies live (default `~/.factory-contained`) |
| `FACTORY_CONTAINED_DRY_RUN=1` | Print what would run; provision nothing |
| `FACTORY_LOG_LEVEL=debug` | Show every command the runtime issues (quiet by default) |

**Nothing crosses into the runtime that you did not ask for.** Variables starting with `FACTORY_`
go in, along with whatever `--forward` names and the variables your inference backend needs — and
nothing else. Your `~/.factory/` is mounted read-write, so config, credential profiles, the project
registry and evolved playbooks work exactly as they do outside. Anything else you want in there —
`~/.claude/projects/`, `GH_TOKEN`, `FACTORY_MANAGED_DIRS`, `FACTORY_VAULT_PATH` — you pass explicitly
with `--mount` or `--forward`.

---

## Troubleshooting

**"podman is installed but its engine is not reachable"** — on macOS the machine stops quietly.
`podman machine start`, or `factory contained setup`, which does it for you.

**"The workspace is read-only inside the runtime"** — the container runs as a user that does not own
your files. Check that the project is owned by you, and that `factory contained verify` is green.

**"could not read ... from inside a container"** — usually the podman machine does not share that
path. On macOS it shares your home directory; a project elsewhere is not mounted at all rather than
mounted empty. Move it under your home directory, or add the path with `podman machine set --volume`
and restart the machine. The launch warns about this before it happens.

**"is not a path the podman machine shares"** — same cause, caught at launch. The message lists the
paths that *are* shared.

**A wall of output instead of three lines** — that is `FACTORY_LOG_LEVEL=debug`. Unset it.

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
| Pre-answering Claude Code's first-run prompts | `factory/contained/claude_state.py` |
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
