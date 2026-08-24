# Frontend Design Mode — Feature-to-UI Pipeline

Frontend design mode builds design-consistent UI features end-to-end. It discovers the project's design system (tokens, components, patterns, infrastructure), produces a constrained UI spec with ASCII mockups for user approval, then builds with design rules enforced and runs a multi-stage QA pipeline. Every new feature matches the existing visual language — no ad-hoc styling, no invented data shapes, no orphaned frontend components.

## Quick Start

```bash
# Build a feature with design system enforcement
factory ceo /path/to/project --mode frontend-design \
  --focus "GPU allocation card on the overview page"

# Resume an interrupted run
factory resume /path/to/project
```

**Note:** Use `factory ceo`, not `factory run`. The `factory run` command restricts `--focus` to improve/research modes.

## Pipeline Overview

```
gate_design_system
  │
  ├── PROCEED (exists) ──▶ staleness_checker ──▶ spec_writer
  │
  └── RELOOP (missing) ──▶ fork_design_research ─┐
                               │                  │
                          5 researchers      join + gate
                          (parallel)              │
                               └──────────────────┘
                                       │
                                       ▼
                               design_auditor ──▶ spec_writer
                                                      │
                                               user approval
                                                      │
                                                      ▼
                                    builder ◀── gate_build (reloop)
                                       │    ◀── gate_render (reloop)
                                       │    ◀── gate_ci (reloop)
                                       │    ◀── gate_review (reloop)
                                       ▼
                               health_checker
                               code_reviewer
                               consistency_tester
                                       │
                                       ▼
                               archivist_build
```

## Phases

### Phase 1: Design Research (5 parallel agents)

Runs only when no design system exists (`.factory/design-system/` is empty). Skipped with a staleness check on subsequent runs.

| Agent | Output | What it captures |
|-------|--------|-----------------|
| `researcher_tokens` | `token-audit.md` | CSS custom properties, color tokens, font families, spacing scale |
| `researcher_components` | `component-inventory.md` | Shared components, props, variant systems, primitive UI library |
| `researcher_patterns` | `pattern-library.md` | Page templates, data-fetching patterns, state management, routing |
| `researcher_ux` | `ux-patterns.md` | Animation choreography, information hierarchy, user-friendliness |
| `researcher_infra` | `infra-context.md` | Backend architecture, API endpoints, deployment environment, **data schemas** |

The infra researcher documents actual data schemas: Pydantic/ORM model fields, types, and write paths. This prevents downstream agents from inventing fields that don't exist in production data.

### Phase 2: Design Auditor

Synthesizes the 5 research artifacts into:
- **`design-baseline.json`** — Structured registry of tokens, components, patterns, and infrastructure
- **`rules.md`** — Hard rules (token purity, component wrappers, accessibility floor) and soft guidelines (spacing, motion, icons)

### Phase 3: Spec Writer

Produces `ui-spec.md` with: component plan, token usage map, layout, state management, dark mode, accessibility, motion choreography, and ASCII wireframe mockups for every state (loading, populated, empty, unreachable).

**User approval gate** — the spec is presented for review before building.

### Phase 4: Builder

Implements the approved spec with design constraints enforced. Key guardrails:

- **Design compliance** — Only approved tokens, declared fonts, shared components
- **End-to-end** — If the frontend needs a backend endpoint that doesn't exist, the builder implements it
- **Infrastructure fidelity** — Backend endpoints use only tools available in the deployment container
- **Data fidelity** — Before reading any field from an existing data structure, the builder traces the write path and verifies the field exists in production code via grep. Test fixtures are not valid evidence. Fields from external APIs (K8s, databases) must be documented in `infra-context.md`.

### Phase 5: Automated Gates

Four sequential gates, each with up to 3 retry loops back to the builder:

1. **gate_build** — `tsc --noEmit` + `npm run lint`
2. **gate_render** — Starts dev server, verifies HTTP 200 on common ports
3. **gate_ci** — Waits for CI checks to pass on the draft PR
4. **gate_review** — Greps code review for `CRITICAL_FOUND`; reloops if found

### Phase 6: QA Pipeline (3 parallel agents)

- **Health checker** — tsc, lint, build, kebab-case naming, dev server smoke test
- **Code reviewer** — Design compliance + data fidelity verification. Flags `CRITICAL_FOUND` for hard violations
- **Consistency tester** — Spacing, border-radius, animation, icon, and status variant analysis

### Phase 7: Archive

Fire-and-forget archivist records the cycle results.

## Data Fidelity Verification

Three-layer defense against reading fields that don't exist in production data (e.g., `config.compute.gpus` when the real field is `nproc_per_node`):

1. **Infra researcher** documents actual Pydantic/ORM model fields with types and traces write paths to confirm which fields are populated. External API fields (K8s, databases) are documented with their source.

2. **Builder** must grep production code (`src/`, `lib/`, `app/`) before reading any field from an existing data structure. Test fixtures don't count as evidence. Fields not written anywhere in production AND not documented as external sources are off-limits.

3. **Code reviewer** verifies every field the new code reads. `CRITICAL_FOUND` if a field is read but never written and not documented as an external source. This triggers the `gate_review` reloop — the builder must fix it.

## Design System Artifacts

All artifacts live in `.factory/design-system/`:

| File | Created by | Purpose |
|------|-----------|---------|
| `token-audit.md` | researcher_tokens | CSS tokens, colors, fonts, spacing |
| `component-inventory.md` | researcher_components | Shared component catalog |
| `pattern-library.md` | researcher_patterns | Page templates, data patterns |
| `ux-patterns.md` | researcher_ux | Animation, hierarchy, UX patterns |
| `infra-context.md` | researcher_infra | Backend architecture, data schemas |
| `design-baseline.json` | design_auditor | Structured design system registry |
| `rules.md` | design_auditor | Hard rules + soft guidelines |
| `ui-spec.md` | spec_writer | Feature spec with ASCII mockups |
| `staleness-report.md` | staleness_checker | Drift verdict (STALE/DRIFT/CURRENT) |

## Known Limitation: Skill Cache

Frontend-design was removed from the graph workflow registry in PR #1346. The `skill_cache.py` module only generates graph-based workflow skills into worktrees. As a result, `factory ceo --mode frontend-design` will fail with "SKILL.md not found" unless the skill is manually placed in the cache.

**Workaround** — copy the SKILL.md into the active cache:

```bash
# Find the current cache checksum
ls ~/.factory/cache/skills/

# Copy the skill into the cache
CHECKSUM=$(ls ~/.factory/cache/skills/)
mkdir -p ~/.factory/cache/skills/$CHECKSUM/workflow-frontend-design
cp skills/workflow-frontend-design/SKILL.md ~/.factory/cache/skills/$CHECKSUM/workflow-frontend-design/
cp skills/workflow-frontend-design/SKILL.annotations.yaml ~/.factory/cache/skills/$CHECKSUM/workflow-frontend-design/
```

This needs to be re-done whenever the cache checksum changes (e.g., after `uv sync` pulls new graph workflow definitions).

## CLI Reference

```bash
# Standard invocation
factory ceo /path/to/project --mode frontend-design \
  --focus "feature description"

# With a specific model
factory ceo /path/to/project --mode frontend-design \
  --focus "dashboard metrics card" \
  --model claude-sonnet-5

# Resume after crash or laptop sleep
factory resume /path/to/project

# Check the dashboard while running
open http://localhost:8420
```
