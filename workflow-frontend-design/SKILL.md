---
name: workflow-frontend-design
description: "Feature-to-UI pipeline that enforces a design system on every new feature. If a design system already exists on disk (from a prior discover run), skips the research phase and goes straight to spec writing with a lightweight staleness check. If no design system exists, runs the full 5-researcher pipeline first. Produces a UI spec constrained by the baseline, gets user approval, builds with discovered design rules enforced, then runs design-specific QA with a two-tier gate (hard failures auto-revert, soft warnings surface for review). Works on any frontend project with a defined token/component system. Use when the user says 'frontend-design', 'design UI for X', or wants design-consistent frontend implementation."
disable-model-invocation: true
argument-hint: "<project_path> --focus <feature description>"
---

# Frontend Design Workflow

The user wants: **$ARGUMENTS**

### Gate — Design System (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
ds=$PROJECT_PATH/.factory/design-system && [ -f $ds/design-baseline.json ] && [ -f $ds/rules.md ] && [ -f $ds/infra-context.md ] && echo PROCEED || echo 'reloop: design system not found'
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `staleness_checker`
- **RELOOP** (exit non-zero / FAIL in output) → return to `fork_design_research` for the next iteration.

*On RELOOP: return to `fork_design_research` (max 3 iterations)*

## Phase 1: Design Research (Parallel)

Spawn 5 agents in parallel:

```bash
factory agent researcher --review-tag tokens --task "Design token research. Find the project's main CSS/theme files (index.css, globals.css, theme.ts, tailwind.config, etc.). Extract every color token, CSS custom property, and theme variable with values for all theme modes. Search all component files for hardcoded color values (hex, rgb, hsl) that bypass the token system. Count frequencies. Document the font families, spacing scale, and border-radius tiers. Write to .factory/design-system/token-audit.md.
Write output to: .factory/design-system/token-audit.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag components --task "Component inventory research. Find the project's component library directory and catalog every shared component — names, props, variant systems. Identify the primitive UI library (Radix, MUI, Chakra, Headless UI, etc.) and which components wrap it. List feature-specific components. Document UI dependencies from package.json. Map composition patterns. Write to .factory/design-system/component-inventory.md.
Write output to: .factory/design-system/component-inventory.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag patterns --task "Layout and pattern research. Read layout.tsx, router.tsx, and every page.tsx in feature modules. Document the shell structure, page templates, data-fetching patterns (e.g. TanStack Query, SWR, Apollo, RTK Query), state management (e.g. Zustand, Redux, Pinia, Context), error handling, motion/animation vocabulary, and accessibility patterns. Write to .factory/design-system/pattern-library.md.
Write output to: .factory/design-system/pattern-library.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag ux --task "UX quality research. Analyze the project's experiential layer: animation choreography (stagger timing, easing curves, entrance sequences, coordinated transitions, duration scale, exit animations, loading states), information hierarchy (heading structure, visual weight, content density, progressive disclosure, data presentation for non-technical users), and user-friendliness patterns (plain language, contextual help, onboarding/empty states, error messages, feedback patterns). Write to .factory/design-system/ux-patterns.md.
Write output to: .factory/design-system/ux-patterns.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag infra --task "Infrastructure context research. Discover the backend deployment architecture by reading Dockerfile, docker-compose.yml, k8s/ manifests, and Helm charts. Identify what environment the backend runs in (container, K8s pod, VM, serverless) and what system tools are available inside the container. Examine the backend API architecture: framework (FastAPI, Flask, etc.), router registration pattern, how new endpoints are added, existing endpoint inventory. Map resource access patterns: how the backend reaches external resources — K8s API via in-cluster config, SSH backends, database connections, external APIs. Document data sources: where data comes from (K8s node resources, subprocess calls, database queries, external APIs) and which client libraries are available. For each data source, document the actual data schemas: read the Pydantic or ORM model definitions, list every field with its type, and trace the write path to confirm which fields are actually populated. Include 1-2 example payloads derived from the model code, not invented. Write to .factory/design-system/infra-context.md.
Write output to: .factory/design-system/infra-context.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
wait
```

**Important:** Run ALL commands above in a **single** Bash tool call with timeout set to at least 600 seconds.

```bash
# Artifact verification: researcher_tokens
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/token-audit.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_tokens: .factory/design-system/token-audit.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_tokens: .factory/design-system/token-audit.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_tokens" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_tokens artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_tokens" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_components
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/component-inventory.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_components: .factory/design-system/component-inventory.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_components: .factory/design-system/component-inventory.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_components" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_components artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_components" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_patterns
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/pattern-library.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_patterns: .factory/design-system/pattern-library.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_patterns: .factory/design-system/pattern-library.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_patterns" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_patterns artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_patterns" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_ux
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/ux-patterns.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_ux: .factory/design-system/ux-patterns.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_ux: .factory/design-system/ux-patterns.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_ux" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_ux artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_ux" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_infra
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/infra-context.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_infra: .factory/design-system/infra-context.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_infra: .factory/design-system/infra-context.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_infra" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_infra artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_infra" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(post-barrier harness verification — DO NOT SKIP)*

## Phase 2: Researcher — Staleness Checker

```bash
factory agent researcher --task "Design system staleness check. Compare design-baseline.json and rules.md against the current codebase for drift. Write verdict (STALE/DRIFT/CURRENT) to .factory/design-system/staleness-report.md.
Write output to: .factory/design-system/staleness-report.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: staleness_checker
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/staleness-report.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: staleness_checker: .factory/design-system/staleness-report.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: staleness_checker: .factory/design-system/staleness-report.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=staleness_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: staleness_checker artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=staleness_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Barrier: Design Research

Wait for all parallel agents to complete: `researcher_tokens`, `researcher_components`, `researcher_patterns`, `researcher_ux`, `researcher_infra`

Read combined outputs: `.factory/design-system/component-inventory.md`, `.factory/design-system/infra-context.md`, `.factory/design-system/pattern-library.md`, `.factory/design-system/token-audit.md`, `.factory/design-system/ux-patterns.md`

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/design-system/component-inventory.md`, `.factory/design-system/infra-context.md`, `.factory/design-system/pattern-library.md`, `.factory/design-system/token-audit.md`, `.factory/design-system/ux-patterns.md`
3. Assess: Verify all five design research artifacts exist and are substantive. token-audit.md must list actual CSS custom properties. component-inventory.md must list actual .tsx files with component names. pattern-library.md must describe actual page layout patterns. ux-patterns.md must describe actual animation, hierarchy, or UX patterns. infra-context.md must describe the deployment environment and backend API architecture. RELOOP if any artifact is empty or clearly fabricated. PROCEED if all five have real data.
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `fork_design_research` (max 3 iterations)*

## Phase 3: Strategist — Design Auditor

```bash
factory agent strategist --task "Design system auditor. Read .factory/design-system/token-audit.md, component-inventory.md, pattern-library.md, ux-patterns.md, and infra-context.md. Synthesize into two outputs: (1) .factory/design-system/design-baseline.json — valid JSON with token_registry, component_inventory, pattern_library, ux_patterns, and infrastructure keys. The infrastructure key must include: deployment (type, orchestrator), container_capabilities (available and unavailable tools), resource_access (how the backend reaches external resources), api_architecture (framework, router pattern, existing endpoints), and data_sources (where data comes from). Extract actual values from the research, do not fabricate. (2) .factory/design-system/rules.md — HARD RULES section (token purity, font family, component wrappers, dark mode parity, accessibility floor, infrastructure fidelity — no unavailable system tools, use established resource access patterns, follow API registration pattern) and SOFT GUIDELINES section (spacing, border-radius, motion choreography, icons, page structure, status colors, information hierarchy, user-friendliness). If previous design-baseline.json exists, merge and flag drift. Preserve any existing MANUAL OVERRIDES section in rules.md.
Read: .factory/design-system/component-inventory.md, .factory/design-system/infra-context.md, .factory/design-system/pattern-library.md, .factory/design-system/token-audit.md, .factory/design-system/ux-patterns.md
Write output to: .factory/design-system/design-baseline.json, .factory/design-system/rules.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: design_auditor
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/design-baseline.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: design_auditor: .factory/design-system/design-baseline.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: design_auditor: .factory/design-system/design-baseline.json is empty" && _vfail=1
_f="$PROJECT_PATH/.factory/design-system/rules.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: design_auditor: .factory/design-system/rules.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: design_auditor: .factory/design-system/rules.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=design_auditor" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: design_auditor artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=design_auditor" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### CEO Review — Audit

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/design-system/design-baseline.json`, `.factory/design-system/rules.md`
3. Assess: Verify design-baseline.json is valid JSON with token_registry, component_inventory, and pattern_library keys. Verify rules.md contains both HARD RULES and SOFT GUIDELINES sections. RELOOP if malformed. PROCEED if structurally valid.
4. Write verdict to `.factory/reviews/ceo-verdict-audit.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `design_auditor` (max 3 iterations)*

## Phase 4: Strategist — Spec Writer

```bash
factory agent strategist --task "UI spec writer. Read .factory/design-system/design-baseline.json, rules.md, and infra-context.md for design system and infrastructure constraints. The feature goal is in the CEO's task prompt (from --focus). Produce .factory/design-system/ui-spec.md with sections: Feature Description, Component Plan (reference existing components, justify any new ones), Token Usage (map each element to specific tokens), Layout, State Management, Dark Mode (both light and dark values), Accessibility, Motion, Visual Mockups, Constraints. For every data-fetching component, specify what it shows when the backend API returns 404 or is unreachable — this must be a designed empty state with guidance text, not an error message. List all API endpoints the feature depends on and whether each already exists in the backend. If an endpoint is missing, specify the backend route, data source, access method (referencing infra-context.md), and response model so the Builder can implement it using only tools available in the deployment environment. VISUAL MOCKUPS: for each designed state (loading, populated, empty, unreachable), draw an ASCII wireframe using box-drawing characters showing the card layout, labels, status indicators, and content hierarchy. The user approves the spec based on these mockups. Be precise — reference actual component names and token values.
Read: .factory/design-system/design-baseline.json, .factory/design-system/infra-context.md, .factory/design-system/rules.md
Write output to: .factory/design-system/ui-spec.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: spec_writer
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/ui-spec.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: spec_writer: .factory/design-system/ui-spec.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: spec_writer: .factory/design-system/ui-spec.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=spec_writer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: spec_writer artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=spec_writer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Steering Point — Spec (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input

*On RELOOP: return to `spec_writer` (max 3 iterations)*

## Phase 5: Builder

```bash
factory agent builder --task "Design-constrained builder. Read .factory/design-system/ui-spec.md (the approved spec), design-baseline.json (the design system), rules.md (the rules), and infra-context.md (infrastructure constraints). Implement exactly what the spec describes. Constraints: only approved color tokens from the baseline, only declared font families, only the project's shared component library (no direct primitive library imports in feature code), established spacing values, dark mode pairs required if the project uses dark mode, aria-labels on interactive elements, the project's established icon library only. CRITICAL: every data-fetching component must handle 3 states: (1) loading/skeleton, (2) populated, (3) unavailable (API 404 or network error). The unavailable state must show a designed message like 'Coming soon' or 'Not yet configured' — NEVER 'Unable to load' or 'Failed to fetch'. Treat missing backend APIs as expected. END-TO-END: if the frontend calls a backend API that does not exist, implement the backend endpoint too. Check the project's API routes — the feature must work end-to-end, not just render a loading spinner. INFRASTRUCTURE: when implementing backend endpoints, check infra-context.md for deployment constraints. Use only system tools available in the container. Use established resource access patterns (e.g., K8s API client, not subprocess calls to unavailable tools). Follow the existing API router registration pattern. DATA FIDELITY: before reading ANY field from an existing data structure (job config, DB record, API response), trace the write path — find the code that creates that record and verify the field exists. Use grep to confirm. If a field is not written anywhere in the codebase, do NOT read it. Refer to infra-context.md Data Schemas for documented fields. Test fixtures for data-consuming endpoints must use field names and values derived from the actual model definitions, not invented. After implementation, start the dev server and verify the feature renders without error messages. Run tests. Commit and open a draft PR.
Read: .factory/design-system/design-baseline.json, .factory/design-system/infra-context.md, .factory/design-system/rules.md, .factory/design-system/ui-spec.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

```bash
# Artifact verification: builder
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/builder-latest.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: builder: .factory/reviews/builder-latest.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: builder artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=builder" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Gate — Build (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && npx tsc --noEmit 2>&1 && npm run lint 2>&1 && echo PROCEED || echo FAIL
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `gate_render`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder` for the next iteration.

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Render (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && ( ROOT='.'; if [ -f package.json ] && node -e "process.exit(JSON.parse(require('fs').readFileSync('package.json','utf8')).scripts?.dev?0:1)" 2>/dev/null; then ROOT='.'; else for d in studio web app frontend client; do if [ -f "$d/package.json" ] && node -e "process.exit(JSON.parse(require('fs').readFileSync('$d/package.json','utf8')).scripts?.dev?0:1)" 2>/dev/null; then ROOT="$d"; break; fi; done; fi; if [ "$ROOT" = '.' ] && ! node -e "process.exit(JSON.parse(require('fs').readFileSync('package.json','utf8')).scripts?.dev?0:1)" 2>/dev/null; then echo 'pass: no dev server script found'; exit 0; fi; cd "$ROOT" && npm run dev </dev/null >/dev/null 2>&1 & DEV_PID=$!; FOUND=0; for i in $(seq 1 30); do for port in 5173 3000 4200 8080; do if curl -s -o /dev/null -w '%{http_code}' http://localhost:$port 2>/dev/null | grep -qE '^(200|304)$'; then FOUND=1; break 2; fi; done; if ! kill -0 $DEV_PID 2>/dev/null; then echo 'reloop: dev server crashed on startup'; exit 0; fi; sleep 2; done; kill $DEV_PID 2>/dev/null; wait $DEV_PID 2>/dev/null; if [ "$FOUND" -eq 1 ]; then echo 'pass: dev server started and responded'; else echo 'reloop: dev server did not respond within 60s'; fi )
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `gate_ci`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder` for the next iteration.

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Ci (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH && ( PR=$(gh pr view --json number -q .number 2>/dev/null) || true; if [ -z "$PR" ]; then echo 'pass: no PR found'; exit 0; fi; for i in $(seq 1 20); do BUCKETS=$(gh pr checks "$PR" --json bucket --jq '.[].bucket' 2>/dev/null) || true; if [ -z "$BUCKETS" ]; then echo 'pass: no CI checks configured'; exit 0; fi; if echo "$BUCKETS" | grep -qE '^(fail|cancel)$'; then NAMES=$(gh pr checks "$PR" --json name,bucket --jq '[.[] | select(.bucket=="fail" or .bucket=="cancel") | .name] | join(", ")' 2>/dev/null); echo "reloop: CI failed for PR #$PR - $NAMES"; exit 0; fi; if ! echo "$BUCKETS" | grep -qE '^pending$'; then echo 'pass: all CI checks passed'; exit 0; fi; sleep 30; done; echo 'reloop: CI timed out after 10 minutes' )
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `health_checker`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder` for the next iteration.

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 6: Health Checker

```bash
factory agent health_checker --task "Design health check. Standard checks (tsc, lint, build) plus: verify kebab-case file naming for new .tsx files, PascalCase exports, no CSS custom property overrides of existing vars. Dev server smoke test: start the dev server, verify it responds with HTTP 200 on a common port (5173, 3000, 4200, 8080). If the server crashes on startup, report as CRITICAL. If no dev server command exists, skip this check.
Read: .factory/design-system/design-baseline.json, .factory/reviews/builder-latest.md
Write output to: .factory/reviews/health-check.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: health_checker
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/health-check.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: health_checker: .factory/reviews/health-check.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: health_checker artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=health_checker" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

## Phase 7: Code Reviewer

```bash
factory agent code_reviewer --task "Design compliance review. Read .factory/design-system/rules.md first. For each changed file check: color usage against the token registry, component imports (no direct primitive library imports in feature code), font usage against declared families, dark mode coverage, accessibility. DATA FIDELITY: For every field the new code reads from an existing data structure (DB record, API response, stored config), verify the field exists in the codebase by checking where it is written. Use CRITICAL_FOUND if a field is read but never written anywhere. Use literal CRITICAL_FOUND for hard rule violations. Use WARNING for soft guideline deviations.
Read: .factory/design-system/rules.md, .factory/design-system/infra-context.md, .factory/reviews/builder-latest.md
Write output to: .factory/reviews/code-review.md" --project "$PROJECT_PATH" --timeout 900
```

```bash
# Artifact verification: code_reviewer
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/code-review.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: code_reviewer: .factory/reviews/code-review.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: code_reviewer artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=code_reviewer" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Gate — Review (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
if grep -q 'CRITICAL_FOUND' $PROJECT_PATH/.factory/reviews/code-review.md; then echo 'reloop: critical design violations found — builder must fix'; else echo 'PROCEED'; fi
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `consistency_tester`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder` for the next iteration.

*On RELOOP: return to `builder` (max 3 iterations)*

## Phase 8: Adversarial Tester — Consistency Tester

```bash
factory agent adversarial_tester --task "Design consistency testing. Run all check scripts in .factory/design-system/checks/ then perform soft checks: spacing analysis, border-radius analysis, animation patterns, icon consistency, status variant usage. Output both .factory/reviews/adversarial_tester-latest.md and .factory/design-system/consistency-report.json with hard_failures, soft_warnings, and summary.verdict fields.
Read: .factory/design-system/design-baseline.json, .factory/design-system/rules.md, .factory/reviews/builder-latest.md
Write output to: .factory/design-system/consistency-report.json, .factory/reviews/adversarial-qa.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: consistency_tester
_vfail=0
_f="$PROJECT_PATH/.factory/design-system/consistency-report.json"
[ ! -f "$_f" ] && echo "VERIFY FAIL: consistency_tester: .factory/design-system/consistency-report.json missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: consistency_tester: .factory/design-system/consistency-report.json is empty" && _vfail=1
_f="$PROJECT_PATH/.factory/reviews/adversarial-qa.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: consistency_tester: .factory/reviews/adversarial-qa.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: consistency_tester: .factory/reviews/adversarial-qa.md is empty" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=consistency_tester" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: consistency_tester artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=consistency_tester" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### CEO Review — Consistency

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/design-system/consistency-report.json`, `.factory/reviews/adversarial-qa.md`
3. Assess: Read .factory/design-system/consistency-report.json. If hard_failure_count > 0, RELOOP to builder with failure details. If only soft_warnings exist, PROCEED (warnings surface in PR). If clean, PROCEED.
4. Write verdict to `.factory/reviews/ceo-verdict-consistency.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### CEO Review — Doc Freshness

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/adversarial-qa.md`
3. Assess: Check the PR diff for documentation freshness. If public APIs, CLI commands, configuration options, or architecture were changed or added, corresponding documentation (README.md, CLAUDE.md, docstrings, --help text, or doc/ files) MUST be updated. PROCEED if docs are current or no doc-worthy changes exist. RELOOP to builder if documentation is stale — specify exactly which changes need doc updates.
4. Write verdict to `.factory/reviews/ceo-verdict-doc-freshness.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder` (max 3 iterations)*

### Gate — Precheck (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
factory precheck $PROJECT_PATH --score-before 0 --score-after 0
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `archivist_build`
- **HALT** (exit non-zero / FAIL in output) → continue to `archivist_build` instead.

## Phase 9: Archivist Build

```bash
factory agent archivist --task "Archive the frontend-design cycle results.
Read: .factory/reviews/adversarial-qa.md
Write output to: .factory/archive/build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
