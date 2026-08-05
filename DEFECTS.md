# `factory contained` — new-user defect report

> **All 21 defects have been addressed** — 18 fixed, 2 documented where the underlying constraint
> cannot be removed, and 1 (D3) partly: the message loops are gone, but publishing the runtime image
> is still outstanding. Each entry carries its status inline. This report is kept as the record of
> what a first-run user hit.

Reviewed against `docs/contained.md` only, from a clean room: virgin `$HOME` (no `~/.factory`, no
`~/.claude`), freshly installed `factory` on `PATH`, all credential and `FACTORY_*` override
variables unset, podman available but its machine stopped, no `~/.kube`.

Target projects: `project/rta` (Go, factory-managed, has `.factory/`) and `project/plain` (plain git
repo, not factory-managed).

## Summary

- **~24 documented surfaces exercised** across ~45 invocations: `--help`, no-args, `verify`, `setup`
  (local ×3 incl. a TTY run, k8s), `bundle` (local + k8s), a real local run, `ls`, `attach`, `sync`,
  `rm` (with and without `--yes`), `FACTORY_CONTAINED_DRY_RUN=1` (local, k8s, division), `--division`
  (real + dry), `--mount`/`--namespace`/`--storage-class` against the wrong target, flags after a
  subcommand, `attach` with no name / a bad name, `--forward`, `--env`, `--live`, `--image`,
  `--name`, `FACTORY_CONTAINED_IMAGE`.
- **Worked exactly as documented:** wrong-target flag rejection, flag-after-subcommand rejection,
  secret redaction in printed commands, `--forward` of an unset variable, `--env` malformed-value
  rejection, "container already exists", the division port-conflict message, reap-and-retry of a
  stopped container, division opt-in/teardown (endpoint really is absent without the flag and really
  is stopped by `rm`), the local run itself (payload executed inside the container against the right
  files), and the conditional provenance assertion set (`factory_state` skipped on `plain`).
- **Did not work as documented, or misled:** 21 defects below.

**The three I would fix first**

1. **D1 — `--namespace` is silently dropped by `attach`/`rm`/`sync` on `--target k8s`.** Every
   cluster lifecycle command in the doc is unusable unless your kube context already names the
   namespace, and the error blames you for not passing a flag you did pass.
2. **D2 — the run's name, which the doc promises is "printed first", is printed last, under ~60
   lines of `argv=[...]` debug logging.** The single most important line of output is the hardest to
   find.
3. **D3 — `setup` is a dead end for every new user.** The image 404s (403s, actually), and the
   printed fix requires a git checkout the user does not have; then the summary tells them to run
   `factory contained setup` — the command that just failed.

**Note on the image workaround.** As briefed, no runtime image is published. D3 records that
experience faithfully. Everything from D2 downward that required a working container
(D2, D6, D7, D9, D13, D16, D17) was exercised with `FACTORY_CONTAINED_IMAGE=localhost/factory-runtime:dev`
and is marked *(needed the image workaround)*.

---

## A. Broken behaviour

### D1. `--namespace` is dropped by `attach`, `rm` and `sync` on `--target k8s` — every documented cluster lifecycle command fails

> **Status: FIXED — namespace threaded into attach/rm/sync; the message no longer blames the flag you passed**

**What I ran**

```
factory contained --target k8s --namespace foo attach k8srun
factory contained --target k8s --namespace foo sync k8srun
```

**What happened** (both, verbatim)

```
contained: no namespace given and the current context does not name one. Pass --namespace, or select one with `oc project <name>`.
```

**What I expected.** The doc advertises these commands directly:

> ```console
> $ factory contained --target k8s sync k8srun
> ```
> ```console
> $ factory contained --target k8s rm k8srun
> ```
> ```
>   attach:  factory contained --target k8s attach k8srun
> ```

and documents `--namespace NS | current context | Never hardcoded`.

**Why it's a problem.** The message is factually false — I passed `--namespace foo`. A new user will
retype the flag, try `--namespace=foo`, then conclude their `oc` install is broken. The root cause is
in `factory/contained/lifecycle.py:400` — `dispatch_lifecycle` calls `attach(name, target)`,
`remove(name, target, ...)`, `sync(name, target)`; none of the three signatures
(`lifecycle.py:227`, `:252`, `:376`) takes a namespace, so `resolve_namespace(None)` is always what
runs. The flag is parsed, scope-checked, and then thrown away.

**Suggested fix.** Thread `args.namespace` through `dispatch_lifecycle` into `attach`/`remove`/`sync`
and on into `resolve_namespace`. Separately, when the k8s path really has no namespace, the message
should distinguish the two cases: "`--namespace` was not given and the current context
(`<context>`) does not name one".

---

### D2. The run's identifier is printed *last*, buried under ~60 lines of debug logging — the doc claims the opposite *(needed the image workaround)*

> **Status: FIXED — identifier printed before provisioning; internal events moved to debug**

**What I ran**

```
factory contained --name rtarun -- backlog-list <cleanroom>/project/rta
```

**What happened** (structure of the real output; the `argv=` lines are single lines of 500–2000 chars each)

```
2026-08-05 10:20:50 [info     ] contained_project_resolved     project=<cleanroom>/… token=<cleanroom>/…
2026-08-05 10:20:50 [info     ] contained_worktree_created     branch=contained/rtarun path=<cleanroom>/…
2026-08-05 10:20:50 [info     ] contained_identity             detail='rootful podman: the workspace is owned by 501:0 inside a container, so the run uses --user 501:0 (group 0 because the runtime image is built for arbitrary UIDs)'
2026-08-05 10:20:50 [info     ] contained_path_rewritten       after=... before=...
Warning: Growth context not configured: ...
Warning: inference is not configured (...)
Warning: macOS: ... is outside ...
2026-08-05 10:20:50 [info     ] contained_step                 argv=['podman', 'run', '-d', '--init', '--name', 'rtarun', '--label', ... ] step=create
2026-08-05 10:20:50 [info     ] contained_step                 argv=[... ] step=assert:project_present
2026-08-05 10:20:51 [info     ] contained_step                 argv=[... ] step=assert:git_usable
2026-08-05 10:20:51 [info     ] contained_step                 argv=[... ] step=assert:factory_state
2026-08-05 10:20:51 [info     ] contained_step                 argv=[... ] step=assert:writable
2026-08-05 10:20:51 [info     ] contained_step                 argv=[... ] step=assert:content_hash
2026-08-05 10:20:51 [info     ] contained_step                 argv=['podman', 'exec', 'rtarun', 'sh', '-lc', 'tmux new-session -d -s factory -c ... \'python3 -c \'"\'"\'import json, os\nspec = json.loads(...)\n ... 40 more lines of escaped Python ... \'] step=run
rtarun
  attach:  factory contained attach rtarun
  result:  factory contained sync rtarun
```

**What I expected.** The doc is explicit, and shows a 5-line transcript:

> ### Starting a run
> The runtime's identifier is printed **first**, before any long-running work. A run whose name you
> cannot see is a run you cannot manage.
>
> ```console
> $ factory contained -- ceo ~/code/rta
> Warning: Growth context not configured: ...
> rta-8ac57c
>   attach:  factory contained attach rta-8ac57c
>   result:  factory contained sync rta-8ac57c
> ```

**Why it's a problem.** The doc's own stated design goal is defeated by the implementation. A new
user's first successful run scrolls a wall of `argv=[...]` and a 40-line embedded Python program past
them; the three lines they actually need are at the bottom, visually indistinguishable from the
debris. It also reads as though something went wrong. This is the single worst first impression in
the feature.

**Suggested fix.** Two separable changes: (a) demote `contained_step`, `contained_path_rewritten`,
`contained_identity` and `contained_project_resolved` to debug level, or gate them behind a
`--verbose` / `FACTORY_LOG_LEVEL`; (b) actually print the name and the attach/sync lines *before*
provisioning starts, as the doc says. If the name genuinely cannot be known before the worktree is
planned, change the doc rather than the claim — but the debug spam should go either way.

---

### D3. `setup` dead-ends, and its own advice cannot be followed

> **Status: PARTLY FIXED — the loop and the unrunnable build hint are gone; publishing the image is still outstanding**

**What I ran**

```
factory contained setup
```

**What happened** (verbatim; note stdout and stderr are interleaved out of order — see D10)

```
Starting machine "podman-machine-default"
...
Machine "podman-machine-default" started successfully
Trying to pull ghcr.io/akashgit/remote-factory/factory-runtime:latest...
Error: unable to copy from source docker://ghcr.io/akashgit/remote-factory/factory-runtime:latest: initializing source docker://ghcr.io/akashgit/remote-factory/factory-runtime:latest: Requesting bearer token: received unexpected HTTP status: 403 Forbidden
Pull failed. If ghcr.io/akashgit/remote-factory/factory-runtime:latest has not been published yet, build it locally:
  podman build -f containers/factory/Containerfile -t ghcr.io/akashgit/remote-factory/factory-runtime:latest .
or point the runtime at an image you already have with FACTORY_CONTAINED_IMAGE.
The podman engine is not reachable. Starting the podman machine...
Pulling the runtime image: ghcr.io/akashgit/remote-factory/factory-runtime:latest
[ok  ] container_engine: podman reachable (5.7.1, rootful)
[FAIL] runtime_image: ghcr.io/akashgit/remote-factory/factory-runtime:latest is not present locally
         fix: factory contained setup --target local  # pulls ghcr.io/akashgit/remote-factory/factory-runtime:latest
[FAIL] inference: no inference configuration found: ...

2 check(s) failed. Fix them, or run `factory contained setup`.
```

Exit code 1.

**What I expected.** The doc's quick start opens with `factory contained setup # pull the image,
check prerequisites`, its transcript shows every check passing, and it says
`factory contained setup` pulls the image; it does not build.

**Why it's a problem.** Three compounding failures for a new user:

1. The advertised image is not pullable (403 — an unauthenticated user cannot even tell whether it is
   private or absent).
2. The offered fix, `podman build -f containers/factory/Containerfile ... .`, is **unrunnable** for
   anyone who installed `factory` as a package. `containers/factory/Containerfile` is a path inside
   the git repository; `pyproject.toml` ships `packages = ["factory"]` only. The user has no such
   file and no clue where to get it. The message does not say "clone the repo first".
3. `runtime_image`'s fix is `factory contained setup --target local`, and the footer is
   `Fix them, or run \`factory contained setup\`` — both instruct the user to re-run the command that
   just failed. That is a loop with no exit.

**Suggested fix.** Publish the image (the real fix). Until then: when the pull fails with 403/404,
say so in those terms and point at a URL, e.g.

```
Could not pull ghcr.io/akashgit/remote-factory/factory-runtime:latest (403 Forbidden).
The image may not be published yet, or may require `podman login ghcr.io`.
Workarounds:
  - use an image you already have:  export FACTORY_CONTAINED_IMAGE=<ref>
  - build it from source:           git clone https://github.com/akashgit/remote-factory && \
                                    cd remote-factory && podman build -f containers/factory/Containerfile -t <ref> .
```

And suppress the "or run `factory contained setup`" footer when the caller *is* `setup`; suppress the
per-check `fix: factory contained setup` line for the same reason.

---

### D4. `bundle`'s own output prints a command the CLI refuses to run

> **Status: FIXED — bundle emits a runnable command and implies the cluster target**

**What I ran**

```
factory contained --target k8s --namespace foo bundle
```

**What happened** — the manifest header contains:

```
# Apply with your own credentials:
#     factory contained bundle --namespace foo | oc apply -f -
```

Following that instruction:

```
$ factory contained bundle --namespace foo
usage: factory contained [runtime flags] -- <factory command>
       factory contained {ls|attach|rm|sync|setup|verify|bundle} [name]
factory contained: error: unrecognized flag '--namespace' after `factory contained bundle`. Runtime flags (--target, --namespace, --name, ...) go before the subcommand, for example:
  factory contained --target k8s bundle
```

**What I expected.** `docs/contained.md` gives the identical command as the way to restore missing
objects:

> Before setup, the same command lists what is missing with the command that restores each — e.g.
> `factory contained bundle --namespace factory-contained | oc apply -f -`.

**Why it's a problem.** The tool generates copy-pasteable instructions that the tool then rejects.
The rejection is at least well-worded, but the user has been sent in a circle by the product itself,
and the same wrong command appears in the published documentation.

**Suggested fix.** Emit `factory contained --namespace foo bundle | oc apply -f -` (flag before
subcommand) in `factory/contained/bundle.py`, in the `k8s_setup` "Nothing was applied" hint, and in
`docs/contained.md`.

---

### D5. A failed launch leaves a git worktree and a branch in the user's repository, with no way to clean them up — the doc promises the opposite

> **Status: FIXED — failed launches roll back; rm prints the worktree and branch cleanup**

**What I ran**

```
factory contained --yes -- backlog-list <cleanroom>/project/rta     # podman machine was down
```

**What happened**

```
2026-08-05 10:18:30 [info     ] contained_worktree_created     branch=contained/rta-4e9fa0 path=~/.factory-contained/rta-4e9fa0/rta
2026-08-05 10:18:30 [warning  ] contained_identity_probe_failed stderr='Cannot connect to Podman. ...'
Error: could not determine who owns .../rta-4e9fa0/rta as seen from inside a container. The probe was: podman run --rm -v ...:...:rw ghcr.io/... stat -c '%u:%g' ... . Run it by hand — a failure here usually means the image is missing (`factory contained setup`) or the podman machine is not running (`podman machine start`).
```

After that, and after `rm`-ing every runtime I ever created, the *source* repository is left with:

```
$ git -C project/rta worktree list
.../project/rta                                    37d5ecd [main]
~/.factory-contained/divrun/rta             37d5ecd [contained/divrun]
~/.factory-contained/divrun2/rta            37d5ecd [contained/divrun2]
~/.factory-contained/rta-4e9fa0/rta         37d5ecd [contained/rta-4e9fa0]
~/.factory-contained/rtarun/rta             37d5ecd [contained/rtarun]

$ git -C project/rta branch --list 'contained/*'
+ contained/divrun
+ contained/divrun2
+ contained/rta-4e9fa0
+ contained/rtarun
```

`contained/divrun2` was created by a launch that **aborted** on the port-conflict check and never
provisioned anything. `contained/rta-4e9fa0` came from the failed launch above.

**What I expected.** The doc's opening promise:

> And it works on a **copy** of your project, so your working tree is untouched and **nothing is left
> behind when the runtime is removed**.

**Why it's a problem.** The user's own repository accumulates worktrees and branches, one per attempt
including failed attempts. `factory contained ls` does not show them, `factory contained rm` does not
remove them, and the doc never mentions that `git worktree list` and `git branch` in *their* project
will grow. Ten experiments later they have ten dangling worktrees and no documented way to reap them.
`rm` does say `Workspace copy remains at ...`, but that discloses only the directory, not the
worktree registration or the branch.

**Suggested fix.** (a) Roll back the worktree + branch when provisioning fails before the first
`[run]` step. (b) Add `factory contained rm --purge <name>` (or a `prune` subcommand) that runs
`git worktree remove` and `git branch -D`. (c) Correct the doc's "nothing is left behind" sentence —
something is, deliberately, and the reader should know that up front. (d) Have `rm` print the exact
cleanup commands:
`git -C <source> worktree remove <path> && git -C <source> branch -D contained/<name>`.

---

### D6. `FACTORY_CONTAINED_DRY_RUN=1 --division` never mentions the division — the one thing you would run a dry-run to preview

> **Status: FIXED — dry-run shows the division banner and the server command**

**What I ran**

```
FACTORY_CONTAINED_DRY_RUN=1 factory contained --division -- backlog-list <cleanroom>/project/rta
```

**What happened.** Grepping the entire output for `division|8430|mcp-server|AUTHENTICATION` returns
only the `.mcp.json` write and the agent brief, both buried inside the escaped `[run]` blob. There is
**no `DIVISION ENABLED` banner** and **no line showing the `podman-mcp-server` launch**. The real run
(D16) prints a boxed banner and starts a server listening on `*:8430`.

**What I expected.** The doc:

> `FACTORY_CONTAINED_DRY_RUN=1` prints the exact commands the real path would run, and provisions
> nothing

and, separately:

> because dry-run's contract is to print *the same argv the real path runs* rather than a tidier
> rendering that could drift from it.

**Why it's a problem.** Dry-run exists so a cautious user can see what is about to happen before it
happens. The single most consequential side effect of `--division` — opening an unauthenticated
container-control endpoint on all interfaces — is exactly the thing dry-run hides. A user who does
the responsible thing (preview first) is *less* informed than one who does not.

**Suggested fix.** Print the `DIVISION ENABLED` banner and a `[division] npx podman-mcp-server ...`
step line in dry-run mode, prefixed to make clear nothing was started.

---

### D7. `ls` reports "Nothing could be listed" when nothing is wrong, and exits 0 when everything is wrong

> **Status: FIXED — 'none' vs 'could not look' vs 'not configured'; non-zero exit on failure**

**What I ran**

```
factory contained ls          # (a) with no runs, podman healthy
factory contained ls          # (b) with the podman machine stopped
```

**What happened** — (a):

```
Nothing could be listed — see the note(s) below.

note: k8s: no namespace given and the current context does not name one. Pass --namespace, or select one with `oc project <name>`.
```

(b):

```
Nothing could be listed — see the note(s) below.

note: local: listing containers failed: Cannot connect to Podman. ...
note: k8s: no namespace given and the current context does not name one. Pass --namespace, or select one with `oc project <name>`.
```

Both exit 0.

**What I expected.** The doc shows `ls` as a plain inventory. "You have no runs" and "I could not
reach the container engine" are different facts.

**Why it's a problem.** (a) is the normal state for a new user who hasn't started anything, and it is
phrased as a failure with a k8s error attached — alarming and irrelevant to someone who has only ever
used `--target local`. (b) is a real failure reported with exit status 0, so any script wrapping `ls`
treats a dead engine as "no runs".

**Suggested fix.** Print `No runtimes.` when both backends listed successfully and returned nothing.
Only print notes for a backend the user plausibly uses — suppress the k8s note when there is no kube
context at all and `--target k8s` was not given. Exit non-zero when a backend failed to list.

---

### D8. Local `verify`'s footer sends the user to a command that cannot fix the failing check; k8s `verify`'s footer names the wrong target

> **Status: FIXED — the footer names only checks setup can repair**

**What I ran**

```
factory contained verify
factory contained --target k8s verify
```

**What happened** — local, with the image already present:

```
[ok  ] container_engine: podman reachable (5.7.1, rootful)
[ok  ] runtime_image: localhost/factory-runtime:dev present locally
[FAIL] inference: no inference configuration found: ...

1 check(s) failed. Fix them, or run `factory contained setup`.
```

k8s:

```
[FAIL] cluster_cli: oc is installed but no current context is selected
         fix: oc login ...  # then `oc project <namespace>`

1 check(s) failed. Fix them, or run `factory contained setup`.
```

**What I expected.** From the doc: "`verify` reports; it changes nothing. Every failure carries the
command that fixes it."

**Why it's a problem.** In the local case, `setup` cannot supply an API key — the only failing check
is one `setup` provably cannot repair, and the user will run it and get the same failure. In the k8s
case the footer names `factory contained setup`, which sets up **local**; the k8s equivalent is
`factory contained --target k8s setup`. A new user who follows it will start a podman machine and
pull an image they did not ask for, and still have no cluster prerequisites.

**Suggested fix.** Make the footer target-aware and check-aware: only offer `setup` when at least one
failing check is one `setup` can act on, and spell the target: `Fix them, or run
\`factory contained --target k8s setup\` for the ones setup can repair (cluster_cli is not one).`

---

### D9. `--division` opens the endpoint on **all** interfaces, and neither the banner nor the doc says so *(needed the image workaround)*

> **Status: DOCUMENTED — bind scope stated in the banner and the guide; loopback is not available**

**What I ran**

```
factory contained --division --name divrun -- backlog-list <cleanroom>/project/rta
lsof -nP -iTCP:8430
```

**What happened**

```
COMMAND     PID    USER   FD   TYPE            DEVICE SIZE/OFF NODE NAME
podman-mc 73249 yizheng    6u  IPv6 0xe12247e63a612a5      0t0  TCP *:8430 (LISTEN)
```

`*:8430` — every interface, not `127.0.0.1`.

**What I expected.** The banner says "Anything that can reach that port can build and run containers
on this host", which is true but leaves the reader to assume localhost. The doc's division section
never states the bind address.

**Why it's a problem.** On a laptop on shared wifi, this is remote code execution as the user, for
the duration of a run that may last hours. That is a materially different risk from a
loopback-only endpoint, and the user is not told which one they have. The banner also offers no
mitigation other than "stop the run".

**Suggested fix.** Bind to `127.0.0.1` by default (the container reaches the host via
`host.containers.internal`, which on the macOS podman machine routes through the gateway — verify
this works before changing, and if it cannot, say so). If it must stay wildcard, state it plainly and
advise: `Listening on 0.0.0.0:8430 — do not use --division on an untrusted network.`

---

## B. Output written for the authors, not for users

The pattern: messages that explain *why the maintainers made a decision*, cite documents the user
cannot open, or name internals, instead of telling the user what to do.

### D10. Spec-section citations in user-facing output (four instances, one of them in the security banner)

> **Status: FIXED — every spec citation removed from runtime output and user docs**

**`factory contained --help`, final paragraph:**

```
The two runtimes share a command surface and an image, not a threat model. Local has no egress
control and holds credentials directly; k8s keeps a restricted SCC and a NetworkPolicy. Neither
confines agent-authored code, and neither replaces review (spec §1.2).
```

**`--division` banner (real run):**

```
  │ Anything that can reach that port can build and run containers on this host —
  │ outside the container boundary, by necessity (§5).
```

**`--live`:**

```
--live is reserved and not implemented. The workspace is a copy by choice, so the host tree is untouched and nothing is left behind (spec §2.2, §3.2).
```

**`docs/contained.md` line 23:**

```
Design and evidence: `docs/superpowers/specs/2026-08-01-factory-contained-runtime-design.md`.
```

**Why it's a problem.** The cited document is not in `mkdocs.yml`'s nav (I checked: `contained.md`
is, `docs/superpowers/**` is not) and is not shipped in the wheel (`packages = ["factory"]`). So a
`pip install`ed user cannot read it at all, and a docs-site reader has no link to click. `§1.2`,
`§5`, `§2.2`, `§3.2` are addresses in a private document. Worse, the division banner's citation is
attached to the one sentence in the whole product where the user is being told about a real security
exposure — the parenthetical implies "the justification is elsewhere", and the elsewhere does not
exist for them.

Also note the doc's own transcript of the banner (line 275) **omits** the `(§5)`, so the published
documentation shows a cleaner message than the product actually prints.

**Suggested fix.** Delete every `§`/`spec` reference from runtime output. If a rationale link is
wanted, publish the design document in the docs site and use a URL. Replacement for `--live`:

```
--live is not implemented. Runs always work on a copy of your project, so your working tree is never modified.
```

### D11. `--help`'s closing paragraph argues with the design instead of orienting the reader

> **Status: FIXED — --help rewritten; subcommands, --yes and env vars now documented**

Quoted in D10. Problems, separately from the `§1.2`:

- **"share a command surface and an image, not a threat model"** is a maintainer's note-to-self about
  an earlier design decision. A user reading `--help` for the first time does not yet know what the
  two targets *are*; they are handed a comparison of their security properties before an explanation
  of their purpose.
- **"Local has no egress control"** with no context is alarming and unactionable. A reasonable new
  user reads this as "using the default mode is dangerous" and stops, or ignores it entirely. There
  is no guidance on when that matters or what to do.
- **"restricted SCC", "NetworkPolicy", "confines agent-authored code"** are unintroduced jargon.
  "SCC" appears nowhere else in `--help`.
- The paragraph also occupies the last thing on screen — the position where a user looks for "what
  do I type next".

**Suggested fix.** Replace with orientation, and move the security comparison behind a pointer:

```
Targets:
  local  a podman container on this machine (default). Fastest to start.
  k8s    a pod on a Kubernetes/OpenShift cluster. For long, unattended runs.

`contained` gives runs a pinned toolchain and works on a copy of your project. It is not a
security sandbox: it does not confine what the agent writes, and it does not replace review.
See `factory contained verify` to check your setup, and the "Contained Runtimes" docs page.
```

Also add the missing content: `--help` documents no subcommand (`ls`/`attach`/`rm`/`sync`/`setup`/
`verify`/`bundle` appear only in the usage line, unexplained), omits `--yes` entirely (it exists and
works — `help=argparse.SUPPRESS` at `factory/cli/contained.py:138`), and omits every
`FACTORY_CONTAINED_*` environment variable including `FACTORY_CONTAINED_DRY_RUN`.

### D12. Provenance failure hints explain the design rather than tell you what to do

> **Status: FIXED — hints lead with cause and a Try: line**

**What the doc advertises** (and what the code at `factory/contained/provenance.py:86-90` prints):

```
contained: step 'assert:git_usable' failed
  git is not usable in the workspace. State detection then reports no_repo, the CEO silently drops
  to build mode, and the eventual error names a flag several steps away from the cause. For a git
  worktree this usually means the source repository's git directory is not mounted — a worktree's
  .git is a *file* pointing at it.
  The container is still there for inspection:
    podman exec -it rta-8ac57c sh
    factory contained rm rta-8ac57c
```

The other four are the same shape — `assert:writable` (`provenance.py:117-120`) explains that "a bind
mount carries the host's ownership through unchanged, so a container whose UID does not own the
mounted tree gets a silently read-only workspace — surfacing several steps later as an agent unable
to explain why its edits vanished"; `assert:factory_state` explains that the factory "will boot as a
fresh project and re-run discovery".

**Why it's a problem.** The first two-thirds of each message is a description of the *bug that would
have happened had the check not existed*. It is genuinely interesting to a maintainer and useless to
a user, who wants one thing: what do I change? The actual cause ("the source repository's git
directory is not mounted") is sentence three. `no_repo`, "the CEO", "build mode", "state detection"
are internal concepts. And the offered next steps (`podman exec ... sh`) are inspection, not repair —
the message never says how to fix it.

**Suggested fix.** Invert: cause, fix, then optionally the rationale. E.g.

```
contained: the workspace is not a usable git repository.
  Likely cause: the source repo's .git directory was not mounted (a git worktree's .git is a file
  pointing elsewhere).
  Try:  factory contained --mount <path-to-real-git-dir> -- <your command>
  Inspect the container first if you prefer:  podman exec -it rta-8ac57c sh
  Then clean up:  factory contained rm rta-8ac57c
```

### D13. Structured log lines leak internal event names into normal output *(needed the image workaround)*

> **Status: FIXED — contained_* events at debug; token= renamed argument=**

Every run prints, at info level, on stdout/stderr:

```
2026-08-05 10:20:50 [info     ] contained_project_resolved     project=/... token=/...
2026-08-05 10:20:50 [info     ] contained_worktree_created     branch=contained/rtarun path=/...
2026-08-05 10:20:50 [info     ] contained_identity             detail='rootful podman: the workspace is owned by 501:0 inside a container, so the run uses --user 501:0 (group 0 because the runtime image is built for arbitrary UIDs)'
2026-08-05 10:20:50 [info     ] contained_path_rewritten       after=/... before=/...
2026-08-05 10:20:50 [info     ] contained_step                 argv=[...] step=create
2026-08-05 10:21:31 [info     ] contained_remove_requested     name=rtarun state=running target=local
2026-08-05 10:21:31 [info     ] contained_remove_completed     name=rtarun
```

**Why it's a problem.** `contained_project_resolved`, `contained_path_rewritten`, `assert:content_hash`
are event identifiers, not English. `token=` sitting next to a filesystem path is actively alarming —
a new user reasonably reads "token" as "credential". The identity line is a paragraph of rationale
about UID mapping and group 0, printed on a *successful* run where nothing is wrong. None of this is
in the doc's transcripts.

**Suggested fix.** Move all `contained_*` events to debug. If one must stay visible, rename the field
(`token=` → `matched_argument=`) and shorten the identity note to `running as UID 501` with the
explanation available only at debug level.

### D14. Three warnings fire on every single run, one of them irrelevant to most payloads

> **Status: FIXED — growth warning conditional; macOS check reads real mounts; ordering changed**

Every launch — dry or real, local or k8s, with any payload — prints:

```
Warning: Growth context not configured: FACTORY_MANAGED_DIRS, FACTORY_VAULT_PATH are unset. Growth dimensions merge 50/50 into the composite score, so eval scores computed in this container are NOT comparable to host scores. Continuing anyway.
Warning: inference is not configured (...). The run will start and every agent call will fail. Fix: ...
Warning: macOS: <project>/.git is outside <HOME>. The podman machine shares $HOME by default, so a path outside it is not mounted at all rather than mounted empty. Add it with `podman machine set --volume` and restart the machine, or move the path under $HOME.
```

**Why it's a problem.**

- The **growth** warning fires even for payloads that compute no eval score at all (`backlog-list`,
  `ls`, `export`). "Growth dimensions", "composite score", `FACTORY_MANAGED_DIRS` and
  `FACTORY_VAULT_PATH` are never explained — the two variables are named in `docs/contained.md`'s
  Environment prose but not in its table and with no description of what to set them to. So the
  warning states a problem, offers no fix, and ends with "Continuing anyway." It will be tuned out by
  run three, which is a real cost given warning three is sometimes important.
- The **macOS mount** warning fired on a run that then **worked perfectly** (D2's `rtarun` executed
  the payload against the right files). It compares the project path to `$HOME` rather than to the
  podman machine's actual mount list, so it is a heuristic that cries wolf.
- The **inference** warning is the useful one and it is sandwiched between two that are not.

**Suggested fix.** Only emit the growth warning for payloads that can produce an eval score, or
demote it to a one-line note with a doc link. Check the real mount list
(`podman machine inspect -f '{{.Mounts}}'`) before warning about `$HOME`. Order the warnings so the
one that will actually break the run comes last (closest to the prompt).

### D15. Interactive `factory contained setup` shows a menu the documentation never mentions, and prints a bare `Error:` on EOF

> **Status: FIXED — EOF defaults to local; the prompt is documented**

**What I ran** (under a pty, stdin closed)

```
script -q /dev/null <cleanroom>/ENTER.sh factory contained setup < /dev/null
```

**What happened**

```
What are you setting up?
  1) local  — a podman container on this machine
  2) k8s    — a pod on a cluster
  3) both
Choice [1]: Error:
```

**What I expected.** The doc's `setup` transcript goes straight into pulling the image; no prompt
appears anywhere in `docs/contained.md`.

**Why it's a problem.** Two things: (a) the documented transcript does not match what a user at a
terminal sees, so they cannot tell whether they are on the same path the doc describes; (b) on EOF
the program prints `Error:` with **no message at all** — an empty error is the least useful possible
output. (The prompt itself is fine and better written than most of the surrounding text.)

**Suggested fix.** Show the prompt in the doc's transcript. Catch `EOFError` in `_ask_target`
(`factory/contained/setup.py:70`) and either default to `local` or print
`No choice given; nothing was set up. Re-run with --target local or --target k8s.`

### D16. The published doc's transcripts are cleaned-up, not captured *(needed the image workaround)*

> **Status: FIXED — transcripts re-captured from a clean install**

Beyond D2 (missing 60 lines of logging) and D10 (missing `(§5)`), the doc's dry-run transcript
renders `[run]` as:

```
[run] podman exec rta-8ac57c sh -lc 'tmux new-session -d -s factory -c … '
```

The real `[run]` line is a ~45-line escaped Python program with nested `'"'"'"'"'"'"'"'"'` quoting.
The doc acknowledges "The `[run]` line is long" — but a reader cannot tell from `…` that it means
"forty-five lines that will look like line noise". Similarly, the doc's `verify` transcript shows one
failing check; a genuinely new user sees three.

**Suggested fix.** Either paste real captures (with an explicit `[…45 lines elided…]` marker so the
scale is honest), or label the transcripts as illustrative. The current form sets expectations the
product does not meet, which is worse than either.

---

## C. Rough edges (lower severity)

### D17. Raw podman output leaks a bare container name into `rm` and relaunch output *(needed the image workaround)*

> **Status: FIXED — podman's stdout no longer echoed**

```
$ factory contained rm rtarun --yes
rtarun                                        <- stray, from `podman rm`
rtarun: deleted. Workspace copy remains at ...
```

and on the reap-and-retry path:

```
reaptest                                      <- stray
reaptest
  attach:  factory contained attach reaptest
```

Cosmetic, but the doubled name reads like a stutter or a bug. Pass `podman rm`'s stdout to
`DEVNULL`. Note the doc's `rm` transcript reproduces this stray line, so it looks intentional.

### D18. Unknown subcommands fall through to the payload path with a baffling error

> **Status: FIXED — typos suggest the intended subcommand**

```
$ factory contained frobnicate
Error: no existing directory found in ['frobnicate']. `factory contained` materializes a workspace from a project already on this machine, for example:
  factory contained -- ceo ~/code/rta
```

A typo (`lst` for `ls`) produces a message about materializing workspaces. `attach`/`rm`/`sync` with a
*wrong but well-formed* name are handled well —
`contained: nosuchrun is not a runtime \`factory contained\` created. \`factory contained ls\` shows
the ones it manages.` — so the good message already exists. Suggest: if the first token is not a path
and is within edit distance 2 of a subcommand, say `unknown subcommand 'lst' — did you mean 'ls'?`.

### D19. `--yes` is accepted after a subcommand while every other flag is rejected there

> **Status: DOCUMENTED — --yes is stated as the exception**

`factory contained rm rtarun --yes` works; `factory contained rm rtarun --target k8s` errors with
"Runtime flags ... go before the subcommand". The doc states the rule absolutely: "anything
flag-shaped after it is an error rather than a name." The exception is deliberate
(`factory/cli/contained.py:164`) and defensible, but it is undocumented, and the error message that
teaches the rule is now teaching a rule with a silent exception. Either document the exception or
accept all runtime flags in both positions.

### D20. `bundle` and `setup` invent a namespace, contradicting "never hardcoded"

> **Status: FIXED — bundle requires a namespace and never invents one**

```
$ factory contained bundle
# factory contained — namespace prerequisites for factory
...
  namespace: factory
```

With `--target local` (the default) and no `--namespace` and no kube context, `bundle` emits cluster
YAML pinned to a namespace literally called `factory`. The doc's table says
`--namespace NS | current context | **Never hardcoded**`, and `factory/contained/k8s.py:107` carries
the comment "Never hardcoded (spec §2.2)". Applying this by accident creates objects in the wrong
place. Suggest: `bundle` should require `--namespace` when no context supplies one, and should refuse
(or warn) when `--target` is `local`, since the output is meaningless there.

### D21. `k8s setup` without a cluster says "About to apply ... with your own oc credentials" when there are none, and contradicts itself in two adjacent lines

> **Status: FIXED — cluster checked first; outcome stated before the YAML**

```
$ factory contained --target k8s --namespace foo setup
Not a terminal, and --yes was not given: nothing will be applied.
About to apply the following to namespace foo with your own oc credentials:
...87 lines of YAML...

Nothing was applied. Hand the manifest above to whoever owns the namespace:
  factory contained bundle --namespace foo | oc apply -f -    <- and this command is rejected (D4)
```

"nothing will be applied" immediately followed by "About to apply the following" is contradictory,
and neither line mentions the actual blocker a new user has — there is no kube context at all, so
nothing *could* have been applied regardless of the TTY. The 87 lines of YAML then bury both
messages. Suggest: check cluster reachability first and say so; when non-interactive, lead with
`Nothing will be applied (not a terminal). The manifest follows; apply it with
\`factory contained --namespace foo bundle | oc apply -f -\`.` and print the YAML last.

---

## Things that worked well (not padding — noted so they are not "fixed")

- `--mount`/`--namespace`/`--storage-class` against the wrong target: rejected at parse time, naming
  the correct target, exactly as documented.
- Flags after a subcommand: rejected with the corrected command spelled out. Best error messages in
  the feature.
- Secret redaction: `--forward GH_TOKEN` with `GH_TOKEN=ghp_supersecret...` printed `GH_TOKEN=<redacted>`,
  and `--env MY_PASSWORD=hunter2` was redacted by name heuristic while `--env PLAIN_VAR=hello` was not.
- `--forward NOT_SET_ANYWHERE` → `Error: --forward NOT_SET_ANYWHERE: not set in this environment`.
- "container 'rtarun' already exists" and "the division port 8430 is already held by the run 'divrun'"
  both name the offending run and offer three concrete options.
- Reap-and-retry of a stopped container works silently, as the troubleshooting section promises.
- The division really is opt-in: no `.mcp.json`, no listener, nothing on 8430 without the flag; and
  `rm` genuinely stops the server (`divrun: division endpoint stopped.`, port released).
- Conditional provenance assertions: `assert:factory_state` present for `rta`, correctly absent for
  the non-factory-managed `plain`.
- The run itself is correct — the payload executed inside the container against the materialized
  worktree and produced the right output.