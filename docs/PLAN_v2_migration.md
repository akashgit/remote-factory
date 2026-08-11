# V2 Migration Plan — factories of factories

> Branch: `ra/v2-attempt` | Source: Akash's 2026-08-10 stand-up vision + deep research of the codebase
> Research report: `docs/RESEARCH_remote_factory_v2_vision.md` (same branch)
> Scope: **Full 0–4 (2 weeks)** | Engine: **both in parallel** (deterministic + skill) | Mode registration: **interim hack, rework in Phase 4**

## Guiding principles

1. Every factory = workflow + own eval. No eval → not a factory.
2. Factory generates modes; modes are registered workflows, composed by reference.
3. Hand-offs are versioned artifacts the reader chooses to read — no direct injection.
4. Preserve what works: 31 validating workflows, Harbor benchmarks, contained runtime.
5. Machine-checkable beats prompt-enforced (deterministic engine = the target for stage boundaries).

## Phase 0 — Baseline & consolidation (1–2 days)

- Write **snapshot preservation tests** for current design/build/improve graphs (node-id sets, edge tuples, conditions) — `tests/test_workflow_research.py:161-251` is the template.
- Quarantine dead code: adversarial GAN loop (document driverless, keep 835 test lines), Distiller/Evaluator doc-only roles, deprecation-set drift (cli/_helpers.py:26-28).
- Fix contract drift: research.md vs research-local.md (researcher.md:83 vs definitions.py:751/1017) — pick one name, update prompts + graph.
- Fix headless `post_checks` non-enforcement (executor.py) — small `_enforce_post_checks` in `_run_agent`; prerequisite for Phase 2 gates (both engines must enforce).
- **Done**: `factory workflow validate` all green, full test suite green, snapshot tests committed.

## Phase 1 — The extraction wave: stages become factories (3–4 days)

**Step 0 (per Akash's advice)**: prompt the model to propose the decomposition — "the factory is materials-in/artifact-out; investigate the codebase and propose primitives" — review its proposal before writing code. The stage registry (`factory/workflow/stages/`) is the primitive-ops layer; it gets built from the model's proposal, reviewed by the team.

**Step 1 — Complete `research-standalone` first (it's the "done" stage, but broken):**
- It has **no PROCEED exit edge** — the gate has no outgoing edges, so a run silently stops (research.py:24-93). Add proper exit wiring.
- It has **no WORKFLOW_META entry** (skill_export.py:42-248) — SKILL.md export falls back to a generic template.
- It's **not in `CEO_MODES`** — `factory ceo --mode research-standalone` fails at argparse (verified). Add with the other modes (Step 7).
- It **lacks RELOOP edges** — a failing gate should loop back to `fork_research`, not halt (definitions.py:431-432 shows the parent pattern).
- Prompt strings are duplicated in the wrapper vs definitions.py:239-279 — unify by importing shared constants instead of copy-paste.

**Step 2 — Extract stage subgraph helpers** (per stage; template `_research_subgraph` definitions.py:154-222, `_deep_qa_subgraph` definitions.py:87-148):
- `_strategy_subgraph()`: strategist + gate_strategy (design-mode user-gate variant stays parametric).
- `_build_subgraph()`: builder + gate_build + artifact post-checks (min_size 500, must_contain "commit").
- `_finalize_subgraph()`: finalize + archivist_build + spec_generate.
- `_shared_prefix_subgraph()`: study→research→gate→strategist→gate (the copy-paste at definitions.py:4024-4087).
- Contract: `def _<stage>_subgraph(*, <params>) -> tuple[dict[str, Any], list[Edge]]`; caller owns entry/exit edges; RELOOP targets wired by caller; fixed node IDs by convention.

**Step 3 — Standalone wrappers** (`factory/workflow/strategy.py`, `build.py`, `finalize.py`; template `research.py`/`deep_qa.py`):
- `meta` dict (name + description) + `workflow()` fn per registry.py:209-238.
- Reads-clearing hack required: `node.model_copy(update={"reads": set()})` (research.py:76-79) — validation demands predecessor writes.
- **RELOOP wiring is mandatory** (research-standalone's silent-stop bug must NOT be repeated): gate→producer loopback edges, not just PROCEED exit.
- Standalone gates that are `evaluator_type="user"` in parents (design's gate_strategy) need an agent/fn fallback for standalone runs — decide per stage.

**Step 4 — Re-wire parents (graph-identical):**
- `build_workflow` (definitions.py:228): replace inline strategy/build/finalize nodes with helper splats (`nodes.update(...)` + `*edges`).
- `design_workflow` (478): inherits via build; verify `just_plan` mutation (definitions.py:673-687) survives strategy extraction.
- `improve_workflow` (721) + `research_workflow` (969): extend to the new helpers (already use `_deep_qa_subgraph`).
- `parallel_improve_workflow` (4011): replace copy-pasted prefix with `_shared_prefix_subgraph()`.
- **Proof**: Phase 0 snapshots must show **zero diff** (tests/test_workflow_snapshots.py). If they change, semantics changed — stop.

**Step 5 — Register + metadata:**
- `_get_builtin_registry` (definitions.py:3952-4004): lazy lambdas for the 3 new standalone workflows.
- `WORKFLOW_META` (skill_export.py:42-248): description + argument_hint per workflow.
- **Bump `tests/test_spec_generate.py:95`**: `len(all_wf) == 31` → **34** (same commit as registration).
- Sweep-all tests auto-extend (good — they validate everything): `test_annotations.py`, `test_skill_export.py` (`test_all_registered_skills_exported`), `test_workflow_definitions.py` (`test_all_validate` + `TestBuilderQaReachability` — build-standalone MUST have edge-reachable deep-QA specialists or it fails).

**Step 6 — Make stages runnable as modes (interim wiring, `# V2-INTERIM`):**
- `CEO_MODES`/`RUN_MODES` (cli/_helpers.py:19-22)
- `CycleState.mode` strict Literal (models.py:520-535) — runtime ValidationError otherwise
- `_detect_incomplete` branch (ceo_completion.py:282-358) — currently "unknown modes assumed complete" — dangerous
- `InnerLoop` mode strings (inner_loop.py:172-176)
- ceo.md mode table
- Verify deterministic engine resolution: mode→`WorkflowRegistry.get_workflow(ceo_mode)` (cli/_ceo_helpers.py:788)
- Check whether stages need custom `--mode` handlers (deep-qa precedent: cli/ceo.py:45-46) or the default path suffices

**Step 7 — Cross-cutting verification (per stage, both engines):**
- **Skill cache**: adding workflows changes the canonical SHA-256 → `~/.factory/cache/skills/<checksum>/` auto-regens (skill_cache.py:30-87) — verify no stale-cache failures; hooks (`settings-<mode>.json` PostToolUse) generate for new modes.
- **Worktree/experiment interplay**: verify `factory workflow run <stage>` inside `.factory-worktrees/` — does standalone `finalize` create experiments/? does `begin/finalize` (store.py:536-643) work in run worktrees? Decide per stage.
- **Events/cycle_analyzer**: stage runs emit events; `cycle_analyzer.py:127` hardcodes `cycle_number=1` — standalone runs may pollute "latest cycle" reconstruction. Decide policy before finalize stage.
- **Docs surface**: CLAUDE.md architecture section, `factory workflow list` output, `factory --help` mode lists, docs/index.md mode tables.
- **Dual-engine e2e test**: heavier (subprocess CLI invocation) — needs `real_worktree`-style markers; commit one per stage.

**Dual-engine done-criterion (per stage)**: (a) deterministic run via `factory workflow run <stage>-standalone <path>` — reads/writes + post-checks enforced; (b) skill run via `factory ceo --mode <stage>-standalone <path>` — SKILL.md playbook + verification hooks active. Snapshot-diff of parent graphs = empty.

**Per-stage deliverable**:
```
factory/workflow/<stage>.py          # standalone wrapper (meta + workflow() + RELOOP wiring)
definitions.py                       # helper + rewired parents + registry entry
skill_export.py                      # WORKFLOW_META entry
tests/test_workflow_<stage>.py       # helper unit + standalone contract + rewiring tests
tests/test_spec_generate.py:95       # 31 → 34 (same commit as registration)
mode wiring (6 files, V2-INTERIM)
dual-engine e2e test                 # deterministic + skill, markers
```

**Stage order** (dependency-aware):
1. **research-standalone completion** (Step 1 — unblocks the pattern for the rest)
2. **strategy** (smallest, no sub-dependencies) — proves the pattern a third time
3. **shared-prefix extraction** (kills copy-paste; de-risks parallel_improve)
4. **build** (largest — deep-QA reachability requirement makes it the riskiest; after strategy)
5. **finalize** (last — touches experiment lifecycle, highest blast radius on store.py contracts)

**Test strategy (order matters)**:
1. Snapshot preservation tests first (done in Phase 0 — the safety net)
2. Helper unit tests (mirror `TestResearchSubgraph`, tests/test_workflow_research.py:27-120: node-id sets, edge tuples, fork targets, post_checks)
3. Standalone contract tests (mirror `TestResearchStandaloneWorkflow`: name, start_node, node-set equality, reads cleared, registry membership, exit edges)
4. Parent rewiring tests (existing preservation tests + snapshot-diff = empty)
5. Sweep-all gates + count fix in the same commit as registration
6. Executor dry-run + dual-engine e2e last

**Timebox**: ~3-4 days solo; ~1 day if the team splits stages (the "15 factories" exercise — one stage per person, this branch is the coordination point).

## Phase 2 — Every factory gets an eval (3 days)

- **Research eval**: new `research_evaluator` role (prompt + `DEFAULT_AGENT_POOL` entry, primitives.py:40-52) writing `.factory/reviews/research-eval.md` with VERIFIED/NOT_VERIFIED per coverage area (mirror of adversarial_tester's evidence contract); fn gate greps `COVERAGE_GAPS` (mirror of `gate_review`, definitions.py:121-131). Wired between gate_research and strategist.
- **Strategy eval**: mechanical fn gate scoring `current.md` structure (parse Phase/Hypothesis blocks via `_parse_hypotheses`, executor.py:1046-1074; each H needs What/Why/Expected-impact/buildability) + **dimension-delta attribution**: strategist tags hypotheses with growth dims → verify the claimed dim moved in `CompositeScore.results` after build (reuse `analysis.py`).
- **Threshold gate helper**: no dedicated numeric-threshold gate node exists; add a small reusable FnNode factory (`factory eval` + threshold compare, JSON verdict via executor.py:948-976).
- **Wire leakage checks** into strategy/precheck gates (research/leakage.py is CLI-only today; docs claim 3 hard gates).
- **Done**: research and strategy standalone factories have ≥1 machine-checkable gate; run both on the repo itself (dogfooding).

## Phase 3 — State hand-off protocol (2–3 days)

- **Manifest**: persist what `NodeTrace` computes (cycle_analyzer.py:49-59, currently dropped at :169-170) → per-cycle `.factory/manifest.json` (node → artifact + SHA-256 + producer + timestamp + read-receipts).
- **Verdict sidecars**: JSON sidecar for every `ceo-verdict-*.md` (role, verdict, rationale, criteria scores, experiment_id, ts) — parse logic exists (report.py:34-54).
- **Hypothesis linkage**: verdict.json references hypothesis id + issue + PR (store.py:588-643) — turns `_detect_incomplete`'s heading-count into a join.
- **Single crash-resume schema**: merge cycle.json/session.json/checkpoint.json into one versioned record.
- **`factory handoff`** (extend `factory export`, cli/store.py:270-284): versioned, hashed, cycle-scoped envelope — the harness-to-harness protocol v0.1.
- **Done**: one factory's output envelope is consumable by another factory/CEO without scanning the filesystem.

## Phase 4 — Composition: factory calls factory (3–5 days)

- **`WorkflowRef` primitive**: node with `workflow: str` + `inputs`/`outputs` mapping; executor resolves via `WorkflowRegistry.get_workflow` and runs a nested `WorkflowExecutor` (pattern exists at executor.py:559-564); child `ExecutionResult` → `parent.node_outputs[child_id]`.
- **Automatic id-namespacing** (kills the manual `dq_rename`, definitions.py:4121-4140); scoped reads/writes; interface-level validation at boundaries (replaces reads-clearing hack).
- **Failure-isolation policy** — pick one: child HALT → parent HALT / degrade / retry (today inconsistent between ForkNode and SubgraphForkNode).
- **Registry-driven mode resolution**: replace hardcoded `CEO_MODES`/`_detect_incomplete`/Literal with registry + trigger — finally uses the dead trigger machinery (primitives.py:286-293). Any registered workflow becomes a mode. Starts from `WorkflowRegistry.get_workflow` + trigger; interim hack must never leak in.
- **Recursive SKILL export**: stage playbooks link child `skills/workflow-<child>/SKILL.md` (files already exist).
- **Done**: `design = research_factory → strategy_factory → build_factory(qa_factory) → finalize_factory` as a graph expression; swarm mode (set of factories with a protocol) demonstrated.

## Phase 5 — Org & benchmarks (parallel/ongoing)

- **The 15-factories exercise**: one stage per person, deliverable = standalone factory + eval + tests + snapshot-diff-unchanged; coordination via repo issues. The repo itself is the test subject.
- **Group benchmarks**: pick 1–2 Harbor targets; fix `gate_verify` to run the real verifier or explicitly defer to Harbor (currently greps builder's own markdown, defaults PASS); pin agent CLIs in Containerfile:92-94.
- Coordinate with in-flight work: OpenCode runner rewrite (backlog H1, touches runners/ + CLI), runner-abstraction-v2 branch, contained runtime.
- Check absorb-vs-conflict on community PRs #1157/#1099/#961 before building WorkflowRef.

## Risks (top)

1. Mode-literal explosion + guard ignorance of new modes (interim hack must not leak into Phase 4 design).
2. Deterministic engine has no crash-resume — stage factories under it lose the completion guard.
3. Headless post_checks gap — fixed in Phase 0, otherwise Phase 2 gates silently pass.
4. Test blast radius (35 files import factory.workflow) — mitigated by snapshot-first + sweep-all tests auto-extending.
5. `origin/v2` dead-code branch vs Phase 0 — rebase/absorb #1165 before starting.

## Timebox

- Week 1 = Phases 0–2 (+ manifest start)
- Week 2 = Phase 3 finish + Phase 4 + hardening
- Akash's 2-day single-person claim covers Phase 0 + 1 with the team doing one stage each.
