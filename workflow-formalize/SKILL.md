---
name: workflow-formalize
description: "Run the formalize workflow."
disable-model-invocation: true
argument-hint: "<project_path>"
---

# Formalize Workflow

The user wants: **$ARGUMENTS**

## Phase 1: Research (Parallel)

Spawn 3 agents in parallel:

```bash
factory agent researcher --review-tag patterns --task "Formalization patterns analysis. Analyze how existing samplers are formalized in $PROJECT_PATH/formal/Mcmc/. Study the pattern: Kernel theory (formal/Mcmc/Kernel/ or Mcmc/Hamiltonian/) -> Executable refinement (formal/Mcmc/Executable/Continuous/) -> CompilerIR program -> IRFormat emission. Read 2-3 existing examples end-to-end (e.g. RWMH: Kernel/GaussianRandomWalk.lean + Executable/Continuous/RWMH.lean + Executable/Continuous/CompilerIR.lean). Document the module structure, naming conventions, import patterns, proof strategies, and how theorems connect kernel specs to executable refinements. Write findings to .factory/strategy/research-patterns.md.
Write output to: .factory/strategy/research-patterns.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag mathlib --task "Mathlib API discovery for the target algorithm. Read the --focus description from the CEO task to understand which algorithm is being formalized. Search $PROJECT_PATH/.lake/packages/mathlib/Mathlib/ for relevant lemmas covering: measure theory, probability, linear algebra, topology, analysis. Check $PROJECT_PATH/formal/lean-toolchain for the pinned Lean/mathlib version. Document available theorems that the formalization can reuse — provide exact module paths and theorem names. Write findings to .factory/strategy/research-mathlib.md.
Write output to: .factory/strategy/research-mathlib.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
factory agent researcher --review-tag algorithm --task "Algorithm specification parsing. Read the --focus description from the CEO task. Parse the algorithm description into a precise mathematical specification. Identify: the state space, the proposal mechanism, the acceptance criterion, what needs to be proved (detailed balance, stationarity, invariance, reversibility), what IR primitives are needed (sample_gaussian, compute_log_density, etc.), and what the signature of the resulting executable function should be. Write findings to .factory/strategy/research-algorithm.md.
Write output to: .factory/strategy/research-algorithm.md" --project "$PROJECT_PATH" --timeout 600 &
```

```bash
wait
```

**Important:** Run ALL commands above in a **single** Bash tool call with timeout set to at least 600 seconds.

```bash
# Artifact verification: researcher_patterns
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-patterns.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_patterns: .factory/strategy/research-patterns.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_patterns: .factory/strategy/research-patterns.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 50 ] && echo "VERIFY FAIL: researcher_patterns: .factory/strategy/research-patterns.md smaller than 50 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_patterns" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_patterns artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_patterns" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_mathlib
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-mathlib.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_mathlib: .factory/strategy/research-mathlib.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_mathlib: .factory/strategy/research-mathlib.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 50 ] && echo "VERIFY FAIL: researcher_mathlib: .factory/strategy/research-mathlib.md smaller than 50 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_mathlib" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_mathlib artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_mathlib" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"

# Artifact verification: researcher_algorithm
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/research-algorithm.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: researcher_algorithm: .factory/strategy/research-algorithm.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: researcher_algorithm: .factory/strategy/research-algorithm.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 50 ] && echo "VERIFY FAIL: researcher_algorithm: .factory/strategy/research-algorithm.md smaller than 50 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=researcher_algorithm" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: researcher_algorithm artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=researcher_algorithm" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(post-barrier harness verification — DO NOT SKIP)*

## Barrier: Research

Wait for all parallel agents to complete: `researcher_patterns`, `researcher_mathlib`, `researcher_algorithm`

### CEO Review — Research

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/strategy/research-algorithm.md`, `.factory/strategy/research-mathlib.md`, `.factory/strategy/research-patterns.md`
3. Assess: Review the three research outputs for the formalization. Check: (1) Are existing formalization patterns well-documented with concrete examples? (2) Are relevant mathlib lemmas identified with exact paths? (3) Is the algorithm specification mathematically precise with clear proof targets? PROCEED if all three are adequate. RELOOP if any research is shallow or missing key details.
4. Write verdict to `.factory/reviews/ceo-verdict-research.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `fork_research` (max 3 iterations)*

## Phase 2: Strategist

```bash
factory agent strategist --task "Synthesize a formalization plan from the three research outputs. Read .factory/strategy/research-patterns.md, research-mathlib.md, and research-algorithm.md. Produce a concrete implementation plan covering: 1) Lean module structure — which files to create under formal/Mcmc/ 2) Theorem statements — what to prove and in what order 3) IR program design — what CompilerIR programs to add 4) IRFormat wiring — how to connect to formal/Mcmc/Executable/IRFormat.lean 5) Mathlib reuse map — which existing lemmas to reference 6) Module dependency graph — build order for lake build 7) formal/Mcmc.lean update plan — new import lines Write the plan to .factory/strategy/current.md.
Read: .factory/strategy/research-algorithm.md, .factory/strategy/research-mathlib.md, .factory/strategy/research-patterns.md
Write output to: .factory/strategy/current.md" --project "$PROJECT_PATH" --timeout 600
```

```bash
# Artifact verification: strategist
_vfail=0
_f="$PROJECT_PATH/.factory/strategy/current.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: strategist: .factory/strategy/current.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: strategist: .factory/strategy/current.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 200 ] && echo "VERIFY FAIL: strategist: .factory/strategy/current.md smaller than 200 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=strategist" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: strategist artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=strategist" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Steering Point — Strategy (User Approval)

**This is a USER approval gate, NOT a CEO review gate. Do NOT self-approve.**

Present the strategy/findings to the user by summarizing key points in your output.
Then explicitly ask the user: "Do you approve this plan, or do you have feedback?"

**You MUST wait for the user's response before proceeding.**
- The user says "approve", "yes", "looks good", or similar → proceed to next step
- The user provides feedback or corrections → re-run the previous step incorporating their feedback
- Do NOT write a verdict file and auto-proceed — this gate requires human input

*On RELOOP: return to `strategist` (max 3 iterations)*

## Phase 3: Archivist Plan

```bash
factory agent archivist --task "Archive the approved formalization plan. Record the algorithm being formalized, the module structure, theorem proof order, and mathlib dependencies.
Read: .factory/strategy/current.md
Write output to: .factory/archive/formalize-plan.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*

## Phase 4: Builder Theory

```bash
factory agent builder --task "Implement the Lean kernel theory and executable refinement. Read the approved formalization plan at .factory/strategy/current.md. Read CLAUDE.md for project conventions. Create the Lean modules specified in the plan: - Kernel theory module(s) under formal/Mcmc/Kernel/ or appropriate subdirectory - Executable refinement module(s) under formal/Mcmc/Executable/ - Refinement theorems connecting the IR program to the mathematical kernel - Module docstrings and public definition docstrings per CLAUDE.md conventions Constraints: No sorry, admit, or axiom. Reuse mathlib lemmas from the plan. After writing the Lean code, run 'cd formal && lake build' to check compilation. If compilation fails, fix the errors before reporting completion. Commit changes when compilation succeeds.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

```bash
# Artifact verification: builder_theory
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/builder-latest.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: builder_theory: .factory/reviews/builder-latest.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: builder_theory: .factory/reviews/builder-latest.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 100 ] && echo "VERIFY FAIL: builder_theory: .factory/reviews/builder-latest.md smaller than 100 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=builder_theory" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: builder_theory artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=builder_theory" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Gate — Theory (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH/formal && lake build
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `fn_theorem_check`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder_theory` for the next iteration.

*On RELOOP: return to `builder_theory` (max 5 iterations)*

## Step: Fn Theorem Check

<!-- command: cd {project_path} && python3 - <<'PYEOF'
import sys, re, subprocess
from pathlib import Path

# Read strategy to extract planned theorem/lemma names
strategy = Path('.factory/strategy/current.md').read_text()

# Extract explicit 'theorem <name>' and 'lemma <name>' declarations
# Matches lines like: '- theorem foo_bar', '  theorem Baz', 'lemma quux'
planned = set()
for m in re.finditer(
    r'\b(?:theorem|lemma)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)',
    strategy,
    re.IGNORECASE,
):
    planned.add(m.group(1))

if not planned:
    print('WARNING: no theorem/lemma names found in strategy — skipping check')
    sys.exit(0)

print(f'Planned theorems/lemmas ({len(planned)}): {sorted(planned)}')

# Find new .lean files via diff against branch point
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
lean_files = [f for f in diff_out.splitlines() if f.endswith('.lean')]

if not lean_files:
    print('ERROR: no .lean files changed under formal/Mcmc/')
    sys.exit(1)

print(f'Checking {len(lean_files)} files: {lean_files}')

# Verify each planned name exists as a declaration without sorry
missing = []
sorry_backed = []
for name in sorted(planned):
    found = False
    for lf in lean_files:
        p = Path(lf)
        if not p.exists():
            continue
        content = p.read_text()
        # Match 'theorem name' or 'lemma name' or 'def name' declarations
        for m in re.finditer(
            rf'^\s*(?:theorem|lemma|def)\s+{re.escape(name)}\b',
            content,
            re.MULTILINE,
        ):
            found = True
            # Check for sorry on same line or next 5 lines
            start = m.start()
            snippet = content[start:].split('\n')[:6]
            if any('sorry' in line for line in snippet):
                sorry_backed.append(f'{name} in {lf}')
            break
        if found:
            break
    if not found:
        missing.append(name)

ok = True
if missing:
    print(f'ERROR: missing declarations: {missing}')
    ok = False
if sorry_backed:
    print(f'ERROR: sorry-backed declarations: {sorry_backed}')
    ok = False

if ok:
    print(f'PASS: all {len(planned)} planned theorems/lemmas found and proved')
sys.exit(0 if ok else 1)
PYEOF -->

Theorem traceability check. Extracts explicit 'theorem X' and 'lemma X' names from current.md, verifies each exists as a declaration in new .lean files (git diff against merge-base), and checks none are sorry-backed. Exits 0 if all present and proved, 1 if any missing or sorry-backed.

```bash
cd $PROJECT_PATH && python3 - <<'PYEOF'
import sys, re, subprocess
from pathlib import Path

# Read strategy to extract planned theorem/lemma names
strategy = Path('.factory/strategy/current.md').read_text()

# Extract explicit 'theorem <name>' and 'lemma <name>' declarations
# Matches lines like: '- theorem foo_bar', '  theorem Baz', 'lemma quux'
planned = set()
for m in re.finditer(
    r'\b(?:theorem|lemma)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)',
    strategy,
    re.IGNORECASE,
):
    planned.add(m.group(1))

if not planned:
    print('WARNING: no theorem/lemma names found in strategy — skipping check')
    sys.exit(0)

print(f'Planned theorems/lemmas ({len(planned)}): {sorted(planned)}')

# Find new .lean files via diff against branch point
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
lean_files = [f for f in diff_out.splitlines() if f.endswith('.lean')]

if not lean_files:
    print('ERROR: no .lean files changed under formal/Mcmc/')
    sys.exit(1)

print(f'Checking {len(lean_files)} files: {lean_files}')

# Verify each planned name exists as a declaration without sorry
missing = []
sorry_backed = []
for name in sorted(planned):
    found = False
    for lf in lean_files:
        p = Path(lf)
        if not p.exists():
            continue
        content = p.read_text()
        # Match 'theorem name' or 'lemma name' or 'def name' declarations
        for m in re.finditer(
            rf'^\s*(?:theorem|lemma|def)\s+{re.escape(name)}\b',
            content,
            re.MULTILINE,
        ):
            found = True
            # Check for sorry on same line or next 5 lines
            start = m.start()
            snippet = content[start:].split('\n')[:6]
            if any('sorry' in line for line in snippet):
                sorry_backed.append(f'{name} in {lf}')
            break
        if found:
            break
    if not found:
        missing.append(name)

ok = True
if missing:
    print(f'ERROR: missing declarations: {missing}')
    ok = False
if sorry_backed:
    print(f'ERROR: sorry-backed declarations: {sorry_backed}')
    ok = False

if ok:
    print(f'PASS: all {len(planned)} planned theorems/lemmas found and proved')
sys.exit(0 if ok else 1)
PYEOF
```

### CEO Review — Theory Review

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Review the compiled Lean proofs. Read the builder output at .factory/reviews/builder-latest.md. Check git diff for the new .lean files. Verify: (1) Module structure matches the approved plan (2) Theorem names and types are meaningful (3) No sorry, admit, or axiom in the code (4) Module docstrings are present PROCEED to IR phase if proofs are well-structured. HALT if fundamental issues require re-planning.
4. Write verdict to `.factory/reviews/ceo-verdict-theory-review.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

## Phase 5: Builder Ir

```bash
factory agent builder --task "Wire IR emission and update imports. Read the approved plan at .factory/strategy/current.md. Read CLAUDE.md for project conventions. Tasks: - Add IR program to CompilerIR (extend the program type with the new sampler) - Wire into formal/Mcmc/Executable/IRFormat.lean - Update formal/Mcmc.lean with new module imports After writing the code, run 'cd formal && lake build' to verify IR matches theory via refinement theorem. If compilation fails, fix the errors before reporting completion. Commit changes when compilation succeeds.
Read: .factory/strategy/current.md
Write output to: .factory/reviews/builder-latest.md" --project "$PROJECT_PATH" --timeout 1200
```

```bash
# Artifact verification: builder_ir
_vfail=0
_f="$PROJECT_PATH/.factory/reviews/builder-latest.md"
[ ! -f "$_f" ] && echo "VERIFY FAIL: builder_ir: .factory/reviews/builder-latest.md missing" && _vfail=1
[ -f "$_f" ] && [ ! -s "$_f" ] && echo "VERIFY FAIL: builder_ir: .factory/reviews/builder-latest.md is empty" && _vfail=1
[ -f "$_f" ] && [ "$(wc -c < "$_f")" -lt 100 ] && echo "VERIFY FAIL: builder_ir: .factory/reviews/builder-latest.md smaller than 100 bytes" && _vfail=1
[ "$_vfail" -ne 0 ] && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_FAIL node=builder_ir" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt" && exit 1
echo "VERIFY OK: builder_ir artifacts validated"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) VERIFY_OK node=builder_ir" >> "$PROJECT_PATH/.factory/hooks/hook-log.txt"
```
*(harness verification — DO NOT SKIP)*

### Gate — Ir (Automated)

**MANDATORY:** Wait for the preceding agent to finish, then run this check BEFORE spawning the next agent. Do NOT run agents in parallel across this gate.

```bash
cd $PROJECT_PATH/formal && lake build
```

- **PROCEED** (exit 0 / no FAIL in output) → continue to `fn_scope_check`
- **RELOOP** (exit non-zero / FAIL in output) → return to `builder_ir` for the next iteration.

*On RELOOP: return to `builder_ir` (max 3 iterations)*

## Step: Fn Scope Check

<!-- command: cd {project_path} && python3 - <<'PYEOF'
import sys, re, subprocess

# Diff against branch point for complete scope view
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

# Get all changed files on this branch
diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base],
    capture_output=True, text=True,
).stdout.strip()
changed = [f for f in diff_out.splitlines() if f]

if not changed:
    print('ERROR: no files changed — vacuous success')
    sys.exit(1)

# Check each file against allowed patterns
allowed = re.compile(
    r'^(formal/Mcmc/.*\.lean'
    r'|formal/Mcmc\.lean'
    r'|reference/.*\.jl'
    r'|Samplers\.ir'
    r'|\.factory/.*)$'
)

violations = [f for f in changed if not allowed.match(f)]
if violations:
    print('ERROR: scope violation — files outside allowed paths:')
    for v in violations:
        print(f'  {v}')
    print()
    print('Allowed: formal/Mcmc/**/*.lean, formal/Mcmc.lean, reference/*.jl, Samplers.ir, .factory/*')
    sys.exit(1)

# Verify at least one new .lean file under formal/Mcmc/
new_out = subprocess.run(
    ['git', 'diff', '--diff-filter=A', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
new_lean = [f for f in new_out.splitlines() if f.endswith('.lean')]

if not new_lean:
    print('ERROR: no new .lean files created under formal/Mcmc/ — vacuous success')
    sys.exit(1)

print(f'PASS: scope clean — {len(changed)} files changed, {len(new_lean)} new .lean files')
for f in new_lean:
    print(f'  + {f}')
sys.exit(0)
PYEOF -->

Scope discipline gate. Diffs against merge-base to verify all changes are within allowed paths (formal/Mcmc/**/*.lean, formal/Mcmc.lean, reference/*.jl, Samplers.ir, .factory/*). Requires at least one new .lean file under formal/Mcmc/ to prevent vacuous success.

```bash
cd $PROJECT_PATH && python3 - <<'PYEOF'
import sys, re, subprocess

# Diff against branch point for complete scope view
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

# Get all changed files on this branch
diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base],
    capture_output=True, text=True,
).stdout.strip()
changed = [f for f in diff_out.splitlines() if f]

if not changed:
    print('ERROR: no files changed — vacuous success')
    sys.exit(1)

# Check each file against allowed patterns
allowed = re.compile(
    r'^(formal/Mcmc/.*\.lean'
    r'|formal/Mcmc\.lean'
    r'|reference/.*\.jl'
    r'|Samplers\.ir'
    r'|\.factory/.*)$'
)

violations = [f for f in changed if not allowed.match(f)]
if violations:
    print('ERROR: scope violation — files outside allowed paths:')
    for v in violations:
        print(f'  {v}')
    print()
    print('Allowed: formal/Mcmc/**/*.lean, formal/Mcmc.lean, reference/*.jl, Samplers.ir, .factory/*')
    sys.exit(1)

# Verify at least one new .lean file under formal/Mcmc/
new_out = subprocess.run(
    ['git', 'diff', '--diff-filter=A', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
new_lean = [f for f in new_out.splitlines() if f.endswith('.lean')]

if not new_lean:
    print('ERROR: no new .lean files created under formal/Mcmc/ — vacuous success')
    sys.exit(1)

print(f'PASS: scope clean — {len(changed)} files changed, {len(new_lean)} new .lean files')
for f in new_lean:
    print(f'  + {f}')
sys.exit(0)
PYEOF
```

## Step: Fn Generate

Emit updated Samplers.ir from the Lean IR programs. Single-shot, no retry.

```bash
cd $PROJECT_PATH && make generate
```

## Phase 6: Qa (Parallel)

Spawn 3 agents in parallel:

Verify committed IR matches Lean source. Fails if IR is stale.

```bash
cd $PROJECT_PATH && make check-generated
```

Run full test suite — Lean compilation + Julia tests including new Reference function.

```bash
cd $PROJECT_PATH && make test
```

<!-- command: cd {project_path} && python3 - <<'PYEOF'
import sys, subprocess, re
from pathlib import Path

# Diff against branch point
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

# Get new/modified .lean files under formal/Mcmc/
diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
lean_files = [f for f in diff_out.splitlines() if f.endswith('.lean')]

# Check 1: sorry/admit/axiom in new .lean files
if lean_files:
    holes_found = False
    for lf in lean_files:
        p = Path(lf)
        if not p.exists():
            continue
        content = p.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if re.search(r'\b(sorry|admit|axiom)\b', line):
                print(f'FAIL: {lf}:{i}: {line.strip()}')
                holes_found = True
    if holes_found:
        print('FAIL: found sorry/admit/axiom in new Lean files')
        sys.exit(1)
    print(f'PASS: no proof holes in {len(lean_files)} files')
else:
    print('WARNING: no .lean files changed under formal/Mcmc/')

# Check 2: IRFormat.lean must be modified
all_changed = subprocess.run(
    ['git', 'diff', '--name-only', base],
    capture_output=True, text=True,
).stdout.strip().splitlines()

irformat_modified = any('IRFormat.lean' in f for f in all_changed)
mcmc_lean_modified = any(f == 'formal/Mcmc.lean' for f in all_changed)

if not irformat_modified:
    print('FAIL: formal/Mcmc/Executable/IRFormat.lean was not modified')
    sys.exit(1)
print('PASS: IRFormat.lean modified')

if not mcmc_lean_modified:
    print('FAIL: formal/Mcmc.lean was not modified (new imports required)')
    sys.exit(1)
print('PASS: formal/Mcmc.lean modified')

print('PASS: all hygiene checks passed')
sys.exit(0)
PYEOF -->

Proof hygiene check. Three checks: (1) No sorry/admit/axiom in new .lean files. (2) IRFormat.lean was modified (IR emission wired). (3) formal/Mcmc.lean was modified (imports updated). Uses merge-base diff for consistent scope.

```bash
cd $PROJECT_PATH && python3 - <<'PYEOF'
import sys, subprocess, re
from pathlib import Path

# Diff against branch point
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip()
if not base:
    print('ERROR: could not determine merge-base with main')
    sys.exit(1)

# Get new/modified .lean files under formal/Mcmc/
diff_out = subprocess.run(
    ['git', 'diff', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
lean_files = [f for f in diff_out.splitlines() if f.endswith('.lean')]

# Check 1: sorry/admit/axiom in new .lean files
if lean_files:
    holes_found = False
    for lf in lean_files:
        p = Path(lf)
        if not p.exists():
            continue
        content = p.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if re.search(r'\b(sorry|admit|axiom)\b', line):
                print(f'FAIL: {lf}:{i}: {line.strip()}')
                holes_found = True
    if holes_found:
        print('FAIL: found sorry/admit/axiom in new Lean files')
        sys.exit(1)
    print(f'PASS: no proof holes in {len(lean_files)} files')
else:
    print('WARNING: no .lean files changed under formal/Mcmc/')

# Check 2: IRFormat.lean must be modified
all_changed = subprocess.run(
    ['git', 'diff', '--name-only', base],
    capture_output=True, text=True,
).stdout.strip().splitlines()

irformat_modified = any('IRFormat.lean' in f for f in all_changed)
mcmc_lean_modified = any(f == 'formal/Mcmc.lean' for f in all_changed)

if not irformat_modified:
    print('FAIL: formal/Mcmc/Executable/IRFormat.lean was not modified')
    sys.exit(1)
print('PASS: IRFormat.lean modified')

if not mcmc_lean_modified:
    print('FAIL: formal/Mcmc.lean was not modified (new imports required)')
    sys.exit(1)
print('PASS: formal/Mcmc.lean modified')

print('PASS: all hygiene checks passed')
sys.exit(0)
PYEOF
```

```bash
wait
```

## Barrier: Qa

Wait for all parallel agents to complete: `fn_check_generated`, `fn_test`, `fn_proof_hygiene`

### CEO Review — Qa

Apply the CEO Review Gate protocol:
1. Read the agent output for the preceding step
2. Read artifacts: `.factory/reviews/builder-latest.md`
3. Assess: Review the three parallel QA results. All three must pass for PROCEED: (1) make check-generated — committed IR matches Lean source (2) make test — full test suite passes (3) proof hygiene — no sorry/admit/axiom in .lean files If any check failed, RELOOP to builder_ir with specific guidance on what to fix (max 3 iterations). If all pass, PROCEED to archivist.
4. Write verdict to `.factory/reviews/ceo-verdict-qa.md`
5. **PROCEED** → continue to next step
6. **REDIRECT** → re-invoke the preceding agent with corrections (max 2)
7. **ABORT** → log failure and skip to archival

*On RELOOP: return to `builder_ir` (max 3 iterations)*

## Step: Fn Manifest

<!-- command: cd {project_path} && python3 - <<'PYEOF'
import json, sys, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

# Diff base for consistent file discovery
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip() or 'HEAD~5'

manifest = {
    'workflow': 'formalize',
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'focus': None,
    'new_lean_files': [],
    'theorems_proved': [],
    'ir_version_bump': False,
    'new_reference_functions': [],
    'qa_results': {
        'check_generated': 'pass',
        'test_suite': 'pass',
        'proof_hygiene': 'pass',
    },
    'retries': {'theory_phase': 0, 'ir_phase': 0},
    'wall_time_seconds': None,
    'disposition': 'success',
}

# New .lean files (added in this branch)
added = subprocess.run(
    ['git', 'diff', '--diff-filter=A', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
manifest['new_lean_files'] = [f for f in added.splitlines() if f.endswith('.lean')]

# Extract theorem/lemma names from new files
for lf in manifest['new_lean_files']:
    p = Path(lf)
    if not p.exists():
        continue
    content = p.read_text()
    for m in re.finditer(r'^\s*(?:theorem|lemma)\s+([a-zA-Z_][a-zA-Z0-9_]*)', content, re.MULTILINE):
        manifest['theorems_proved'].append(m.group(1))

# Check IRFormat.lean modification
ir_diff = subprocess.run(
    ['git', 'diff', '--stat', base, '--', 'formal/Mcmc/Executable/IRFormat.lean'],
    capture_output=True, text=True,
).stdout.strip()
manifest['ir_version_bump'] = bool(ir_diff)

# Extract new Julia reference functions
jl_diff = subprocess.run(
    ['git', 'diff', '--diff-filter=AM', '--name-only', base, '--', 'reference/'],
    capture_output=True, text=True,
).stdout.strip()
for jl_file in jl_diff.splitlines():
    p = Path(jl_file)
    if p.exists() and p.suffix == '.jl':
        content = p.read_text()
        for m in re.finditer(r'^\s*function\s+([a-zA-Z_][a-zA-Z0-9_!]*)', content, re.MULTILINE):
            manifest['new_reference_functions'].append(m.group(1))

Path('.factory/manifest.json').write_text(json.dumps(manifest, indent=2))
n_files = len(manifest['new_lean_files'])
n_thms = len(manifest['theorems_proved'])
print(f'Manifest written: {n_files} new files, {n_thms} theorems, IR bump={manifest["ir_version_bump"]}')
sys.exit(0)
PYEOF -->

Write .factory/manifest.json with structured workflow metadata: new .lean files, theorems proved, IR version bump, reference functions, QA results, retry counts, timing, and disposition.

```bash
cd $PROJECT_PATH && python3 - <<'PYEOF'
import json, sys, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

# Diff base for consistent file discovery
base = subprocess.run(
    ['git', 'merge-base', 'HEAD', 'main'],
    capture_output=True, text=True,
).stdout.strip() or 'HEAD~5'

manifest = {
    'workflow': 'formalize',
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'focus': None,
    'new_lean_files': [],
    'theorems_proved': [],
    'ir_version_bump': False,
    'new_reference_functions': [],
    'qa_results': {
        'check_generated': 'pass',
        'test_suite': 'pass',
        'proof_hygiene': 'pass',
    },
    'retries': {'theory_phase': 0, 'ir_phase': 0},
    'wall_time_seconds': None,
    'disposition': 'success',
}

# New .lean files (added in this branch)
added = subprocess.run(
    ['git', 'diff', '--diff-filter=A', '--name-only', base, '--', 'formal/Mcmc/'],
    capture_output=True, text=True,
).stdout.strip()
manifest['new_lean_files'] = [f for f in added.splitlines() if f.endswith('.lean')]

# Extract theorem/lemma names from new files
for lf in manifest['new_lean_files']:
    p = Path(lf)
    if not p.exists():
        continue
    content = p.read_text()
    for m in re.finditer(r'^\s*(?:theorem|lemma)\s+([a-zA-Z_][a-zA-Z0-9_]*)', content, re.MULTILINE):
        manifest['theorems_proved'].append(m.group(1))

# Check IRFormat.lean modification
ir_diff = subprocess.run(
    ['git', 'diff', '--stat', base, '--', 'formal/Mcmc/Executable/IRFormat.lean'],
    capture_output=True, text=True,
).stdout.strip()
manifest['ir_version_bump'] = bool(ir_diff)

# Extract new Julia reference functions
jl_diff = subprocess.run(
    ['git', 'diff', '--diff-filter=AM', '--name-only', base, '--', 'reference/'],
    capture_output=True, text=True,
).stdout.strip()
for jl_file in jl_diff.splitlines():
    p = Path(jl_file)
    if p.exists() and p.suffix == '.jl':
        content = p.read_text()
        for m in re.finditer(r'^\s*function\s+([a-zA-Z_][a-zA-Z0-9_!]*)', content, re.MULTILINE):
            manifest['new_reference_functions'].append(m.group(1))

Path('.factory/manifest.json').write_text(json.dumps(manifest, indent=2))
n_files = len(manifest['new_lean_files'])
n_thms = len(manifest['theorems_proved'])
print(f'Manifest written: {n_files} new files, {n_thms} theorems, IR bump={manifest["ir_version_bump"]}')
sys.exit(0)
PYEOF
```

## Phase 7: Archivist Build

```bash
factory agent archivist --task "Archive the formalization build results. Record: what algorithm was formalized, theorems proved, IR programs added, new Reference Julia functions generated, and any lessons learned from proof compilation iterations.
Read: .factory/reviews/builder-latest.md
Write output to: .factory/archive/formalize-build.md" --project "$PROJECT_PATH" --timeout 300 --model haiku &
```
*(fire-and-forget — CEO continues immediately)*
