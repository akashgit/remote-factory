# `factory contained` — Verification Points

The rubric for judging an implementation of `factory contained` from captured evidence alone.

You are given exactly two things: an `evidence.jsonl` file produced by
`scripts/eval-contained/collect.sh`, and this document. You are **not** given the implementation
source, and you must not go looking for it. A judge who reads the code decides the code looks
correct; that verdict is worthless. Your verdict is worth something only because it rests on
captured behavior.

## The evidence file

One JSON object per line. Four record types.

| `record` | Meaning |
|---|---|
| `meta` | Run identity: timestamp, git commit, host arch, tool versions, pinned OpenShell version. |
| `coverage` | Which tiers were requested, which ran, which were skipped and why. Exactly one per run. |
| `probe` | One criterion's captured behavior. Carries `id`, `tier`, `status`, `command`, `exit_code`, `stdout`, `stderr`, and `observations`. |
| `error` | A probe that crashed. The criterion is unproven, which is not the same as failed. |

`probe.status` is one of:

- `ok` — the probe ran and captured behavior. **You** decide pass/fail from its contents.
- `skipped` — the tier gating this criterion was unavailable. Carries `reason`.
- `not_applicable` — the criterion belongs to an implementation phase later than the one under
  judgement. Carries `phase`.

`status: ok` does **not** mean the criterion passed. It means the probe collected evidence. The
probe never decides; that is your job.

## Invariants

These MUST hold for the run to be judged at all. Check them before judging any criterion.

- [ ] Exactly one `coverage` record is present
- [ ] Every criterion named in `coverage.criteria` has exactly one corresponding `probe` or
      `error` record — an `error` record satisfies this, and forces `FAIL` for that criterion
- [ ] No `probe` record with `status: skipped` is reported by you as passing
- [ ] Every `PASS` you issue quotes a `probe` record: the command, its exit code, and the specific
      matched output
- [ ] `meta.openshell_version` is recorded (the literal `absent` is a valid value)

## Criteria

Each criterion's `id`, tier, phase, and weight come from the `coverage.criteria` list in the
evidence, which mirrors `scripts/eval-contained/criteria.tsv`. Use the weight given there. Do not
substitute your own.

### Inference

| ID | Passes when |
|---|---|
| C1 | The composed sandbox environment contains neither `CLAUDE_CODE_USE_VERTEX` nor `CLOUD_ML_REGION`, and does contain `ANTHROPIC_BASE_URL=https://inference.local`. The probe sets both Vertex variables in the host environment first; if they are absent from the host, the probe proves nothing and this is a `FAIL`, not a `PASS`. Additionally, **none of the credential variables the probe set on the host may appear in the composed environment** — credentials belong on the gateway (spec §8), and a forwarded one lands in the sandbox argv and in this evidence file. Judge this by **key presence**, not by value: composed environments are redacted before printing, so a leaked secret appears as `<redacted>` and a value-based check would call a leak clean. `ANTHROPIC_API_KEY=unused` is the pinned placeholder and must be present; any other credential key from `observations.host_credential_vars_set` appearing in `observations.sandbox_env` is a `FAIL`. |
| C2 | `ANTHROPIC_BASE_URL` is exactly `https://inference.local`. A trailing `/v1`, a trailing slash, or any suffix is a `FAIL` — Claude Code appends `/v1/messages` itself. |
| C3 | The composed `claude` argv contains `--bare` when the factory runs in sandbox mode. |
| C4 | The composed `claude` argv does **not** contain `--bare` for an ordinary non-sandbox invocation. C3 and C4 must both hold; a `--bare` that is always present fails C4 and the pair is worth nothing on its own. |

### Filesystem context

| ID | Passes when |
|---|---|
| C5 | `.factory/config.json` is present inside the sandbox **and** `results.tsv` still has all 3 rows. The probe must state that the test project's `.gitignore` lists `.factory/`; if it does not, the probe does not test the trap and this is a `FAIL`. |
| C6 | A deliberately broken transfer exits non-zero with a message naming `.factory/`. An exit code of 0, or a message that does not name `.factory/`, is a `FAIL` — a silent fresh-project boot is the exact failure this criterion exists to catch. |
| C7 | `factory detect` inside the sandbox reports the same `ProjectState` as the host run recorded in the same probe. |
| C8 | The generated driver-config JSON's bind `source` equals the project path **exactly**. `$HOME`, `/`, or any parent of the project path is a `FAIL`. |
| C9 | The top-level driver-config key is `podman` against a podman-backed gateway and `docker` against a docker-backed gateway. Both must appear in the evidence. |
| C10 | `--division=local` against a gateway with `enable_bind_mounts` unset exits non-zero with a message that names the setting, and no sandbox was provisioned. |

### Build plane

| ID | Passes when |
|---|---|
| C11 | The rendered build-pod manifest contains no `privileged: true`, no `SYS_ADMIN`, and no `hostPath`. On a cluster run, the pod additionally carries `metadata.annotations."openshift.io/scc"` equal to the SCC recorded in `meta`. |
| C12 | The rendered manifest sets `BUILDAH_ISOLATION=chroot`, mounts an `emptyDir` at the container-storage path, and provides subuid/subgid ranges. `BUILDAH_ISOLATION=oci` is a `FAIL`. |
| C13 | Applying `--pod-patch` changes only the patched field, and C11 and C12 still hold on the patched output. |
| C14 | A manifest supplied via `--pod-manifest` is used verbatim, and a privileged override produces a warning in `stderr`. Silent acceptance is a `FAIL`. |
| C15 | Resources target the namespace of the kubeconfig's current context. |
| C16 | `--namespace other-ns` makes resources target `other-ns`. |
| C17 | The literal `factory-division` appears nowhere in the implementation outside documentation and tests. The probe reports the grep hits; any hit in implementation source is a `FAIL`. |
| C18 | The rendered division MCP policy's tool-name rules equal the intended set exactly. An extra tool is a `FAIL`. A wildcard is a `FAIL`. |
| C19 | **The criterion that matters.** All four legs must appear in the evidence: (1) the first build fails and its error text is captured, (2) the error text reached the agent, (3) a second build after the fix succeeds, (4) a validation container ran and its exit code is reported. Three of four is a `FAIL`. Build and validation must be on the **same architecture** — a probe that builds on one arch and validates on another is invalid; report it as `FAIL` with that reason. |
| C20 | `events_list` output contains `ImagePullBackOff` for a manifest naming a nonexistent image. A timeout with no diagnosis is a `FAIL`. |

### Regression and guardrails

| ID | Passes when |
|---|---|
| C21 | The composed `tmux` command for the fixed arg set is byte-identical to the recorded golden. The probe reports a diff; a non-empty diff is a `FAIL`. |
| C22 | `factory contained --tmux-persist` fails at **parse time**. Evidence of a runtime failure — a provisioning attempt, a traceback from inside the run — is a `FAIL` even though the exit code is non-zero. |
| C23 | A bare `--division` with no value is a parse error. Silently inheriting the `--target` value is a `FAIL`. |
| C24 | With `FACTORY_MANAGED_DIRS` and `FACTORY_VAULT_PATH` both unset, a warning naming **both** appears on stderr **and the exit code is 0**. A non-zero exit is a `FAIL` — this criterion is specifically that the factory warns and continues. |
| C25 | With both variables set, both appear in the composed sandbox environment. |
| C26 | The sandbox is discoverable by label via `openshell sandbox list --selector`, and no `tmux_sessions.json` analogue was written. |

## Failure modes

| Signal in evidence | Indicates |
|---|---|
| `coverage.tiers_skipped` includes t2 or t3, and the verdict is `PASS` | Absent-dependency pass — the verdict must be `INCOMPLETE` |
| A `probe` with `status: skipped` reported as passing | Skip counted as success; the whole run is untrustworthy |
| `probe.observations` present but `stdout` empty and `command` absent | Probe asserted rather than captured; the criterion is unproven |
| C1 passing while `observations.host_env` lacks the Vertex variables | Vacuous pass — nothing was stripped because nothing was set |
| C3 passing and C4 also passing, but both probes ran with identical environments | The sandbox/non-sandbox distinction was not exercised |
| C19 with a successful second build but no validation-container exit code | The loop does not close; the interesting half is missing |
| C19 where the build host arch differs from the validation host arch | Invalid probe (eval plan §10.4) — reject rather than pass or fail on the output |
| Two `probe` records for the same `id` with different `status` | Ambiguous evidence; report `FAIL` and name the duplication |
| `error` records for criteria you report as `PASS` | A crashed probe cannot prove anything |

## Output

Emit the factory's eval JSON contract and nothing else — no prose before or after:

```json
{"results": [
  {"name": "C3", "score": 1.0, "weight": 1.0, "passed": true,
   "details": "PASS: `factory agent researcher` with FACTORY_SANDBOX=1 (exit 0) composed argv [...] containing `--bare`"},
  {"name": "C5", "score": 0.0, "weight": 2.0, "passed": false,
   "details": "SKIPPED: t2 unavailable — openshell not installed"},
  {"name": "overall", "score": 0.0, "weight": 0.0, "passed": false,
   "details": "INCOMPLETE: t2 and t3 were skipped; 5/5 phase-1 criteria passed"}
]}
```

Rules for the output:

- One result per criterion in `coverage.criteria`, named by its ID. No extras, no omissions.
- `weight` is copied from the evidence. Never adjusted.
- `details` begins with `PASS:`, `FAIL:`, `SKIPPED:`, or `NOT_APPLICABLE:`.
- A `PASS` quotes the command, the exit code, and the matched output.
- A final `overall` result with `weight: 0.0` whose `details` begins with `PASS:`, `FAIL:`, or
  `INCOMPLETE:`, and states how many in-phase criteria passed out of how many.
- `overall` is `INCOMPLETE` whenever t2 or t3 was skipped, regardless of how many static criteria
  are green. It is `FAIL` if any in-phase criterion failed. `FAIL` outranks `INCOMPLETE`.

## Forbidden actions

- Reading, searching for, or asking for the implementation source. If it is present in the working
  directory, do not open it.
- Running the implementation, the collector, or any probe yourself. You judge a completed
  collection; you do not extend it.
- Justifying a `PASS` with "looks correct", "should work", "appears to", or any reasoning not
  grounded in a quoted evidence record. Doing this is itself a reportable failure: say so in the
  `overall` details.
- Reporting a criterion absent from the evidence as anything other than `FAIL` with the reason
  "no probe record".
- Adjusting any weight, or reporting `PASS` for `overall` when t2 or t3 was skipped.
