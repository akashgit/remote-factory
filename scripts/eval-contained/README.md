# Evaluating `factory contained`

Three parts, deliberately owned by three different things.

| Part | What it is | Sees |
|---|---|---|
| **Collector** | `collect.sh` → `_collector.py` → `probes/*` | The repository. No model in the loop. |
| **Judge** | `factory agent contained_evaluator` | `evidence.jsonl` and the rubric. **Not** the source. |
| **Meta-eval** | `pytest tests/eval_contained/` | Both, because its job is to distrust them. |

The separation is the point. A judge shown the implementation decides the implementation looks
correct; withholding it is what makes a green verdict mean something.

## Running it

```bash
# Static + dry-run only. Reports INCOMPLETE by design — no sandbox was exercised.
scripts/eval-contained/collect.sh --tiers t0,t1 --phase 1 --out evidence.jsonl --validate

# With a local gateway and a running podman machine
scripts/eval-contained/collect.sh --tiers t0,t1,t2 --phase 3 --out evidence.jsonl

# With a cluster as well
scripts/eval-contained/collect.sh --tiers t0,t1,t2,t3 --phase 4 --out evidence.jsonl

# Then judge it. The evaluator must not be able to reach the implementation source.
factory agent contained_evaluator --task "judge $PWD/evidence.jsonl" --project /path/to/evidence-dir
```

`--phase N` matters: criteria introduced by a later phase are reported `NOT_APPLICABLE`, never
`FAIL`, so "not built yet" is distinguishable from "built wrong".

## A verdict is only as good as its evidence

The judge reports faithfully on what it is given, which means a corrupted collection produces a
confident, well-argued, wrong verdict. This has already happened once: a collection run concurrently
with the meta-eval had its fixtures overwritten mid-run, and the resulting evidence showed one tmux
case emitting another case's command and a captured argv going missing. The judge correctly returned
`FAIL` — on evidence that described a run that never happened. The `flock` in `_probe_lib` exists
because of that, and `--validate` catches structural damage, but neither can tell you the *contents*
of a record are real.

So: collect on a quiet machine, and treat a surprising `FAIL` as a reason to look at the evidence
before looking at the code.

## Before trusting any verdict

```bash
pytest tests/eval_contained/ -v
```

This applies each mutant in `tests/eval_contained/mutants/` and asserts the criterion assigned to it
can no longer pass. A mutant that survives means that criterion is decorative and must be fixed
before the verdict on the real implementation is worth reading. It also checks the skip semantics —
that a tier which did not run can never be reported as passing — which is the evaluation's own
honesty check and has to hold first.

## Tiers and skip semantics

| Tier | Requires | Covers |
|---|---|---|
| t0 | nothing | Generated artifacts: manifests, MCP policy, forbidden-literal scans |
| t1 | nothing | Composed command lines, via dry-run and PATH-shimmed binaries |
| t2 | `openshell`, a running podman/docker, **and a registered gateway that answers** | Real sandbox, real bind mount, real local division build |
| t3 | a reachable cluster | Real build pod, real validation pod, `events_list` diagnostics |

Every clause of the t2 requirement is load-bearing. Having the `openshell` binary on `PATH`
provisions nothing, and a gateway that is registered but not answering provisions nothing either —
so the collector probes the gateway with a real listing rather than trusting that it exists. Treating
a partial environment as available is the absent-dependency false pass in its purest form: probes run
against a dead gateway and report failures that belong to the machine rather than to the
implementation, and a reader cannot tell the difference from the evidence.

A tier that cannot run is **reported as skipped with a reason**, never omitted and never counted as
a pass. Overall status is `INCOMPLETE` — not `PASS` — whenever t2 or t3 was skipped, however many
static criteria are green. An implementation cannot be declared correct on static analysis alone.

## Files

```
criteria.tsv          the 26 criteria: tier, phase, weight, pass condition. Single source of truth.
collect.sh            thin wrapper; locates the interpreter
_collector.py         tier detection, phase gating, probe dispatch, evidence validation
probes/_probe_lib.py  shared plumbing: fixed paths, explicit environments, capture helpers
probes/t<N>_*.py|sh   one file per criterion group; declares `# COVERS: C3,C4`
golden/               recorded composed command lines for byte-identity comparison (C21)
regen_mutants.py      mutant fault definitions; regenerates the .patch files when source moves
```

## Adding a probe

1. Create `probes/t<tier>_<slug>.py` with a `# COVERS: C<n>[,C<m>]` line in its first 20 lines.
2. Emit one `probe_record(...)` per criterion it covers. Report **facts**, not verdicts — an argv
   list, a diff, a captured exit code. The judge decides; a probe that decides has replaced the
   judge with itself.
3. Add the criterion's pass condition to `docs/expected-behaviors/contained/verification-points.md`
   if it is not already there. The meta-eval fails if a criterion in `criteria.tsv` is missing from
   the rubric.
4. Add a mutant in `regen_mutants.py` that breaks the behavior, and assert the criterion catches
   it. An untested criterion is an assumption.

Probes run under fully-explicit environments at fixed paths under `/tmp/factory-eval-contained`.
Both are deliberate: an ambient environment variable or a random temp directory leaks into a
composed command line and makes golden comparison impossible. The cost is that two probe runs would
trample each other's fixtures, so `_probe_lib` takes an exclusive `flock` on
`/tmp/factory-eval-contained.lock` at import and holds it for the process. A collection started
while the meta-eval is running therefore queues rather than corrupting it — which matters because
the corruption presents as a mutant "surviving", indistinguishable from a genuinely decorative
criterion.

## What this cannot catch

Recorded so the verdict is not over-read. OpenShell's own correctness — it is alpha, and its version
is pinned and recorded in every evidence file so a t2/t3 failure can be attributed to the runtime.
Long-horizon loop quality — C19 proves one build-fix-rebuild cycle closes, not that twenty cycles
converge. The safety of the division's design — the criteria check that the isolation boundary is
opened only as narrowly as designed, not that opening it is wise. And latency, which is unmeasured.
