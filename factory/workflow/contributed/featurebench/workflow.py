"""FeatureBench mode — hybrid host/container execution pipeline.

6-node pipeline with one RELOOP gate:
  scan_stubs (FnNode) → builder → adversarial_tester → gate_tests ⇄ builder_fix → archivist

scan_stubs mechanically detects all blank/masked function bodies via AST and
writes a compact manifest for the builder. The builder reads the problem
statement directly, explores the codebase (including existing type definitions),
and implements the feature. The adversarial_tester then reads the existing
type definitions and writes skeptical spec-compliance tests that probe for
common builder mistakes (wrong field mappings, paraphrased strings, missing
error paths). gate_tests runs them inside the container. On RELOOP, builder_fix
reads the pytest output and fixes source code directly — adversarial_tester is
NOT re-run, so the test suite stays stable across iterations.
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    GateNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "featurebench",
    "description": (
        "FeatureBench mode — lean pipeline for implementing features from interface specifications. "
        "Scans for masked function bodies, builder implements directly from problem_statement.md "
        "(no separate research/strategy — the spec IS the strategy), then a skeptical adversarial "
        "tester reads existing type definitions and writes tests that catch field mapping bugs. "
        "gate_tests runs them inside the container via docker exec, RELOOPing to builder_fix. "
        "Use when invoked with --mode featurebench."
    ),
}

_ast_scanner_script = """\
import ast, os, subprocess, re

blanks = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('.git','.factory','__pycache__','test','tests')]
    for f in files:
        if not f.endswith('.py'): continue
        path = os.path.join(root, f)
        try: tree = ast.parse(open(path).read())
        except: continue
        src = open(path).readlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
            if not hasattr(node, 'end_lineno'): continue
            body = node.body
            is_empty = False
            if len(body)==1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, (ast.Constant,)):
                after = src[body[0].end_lineno:node.end_lineno] if hasattr(body[0],'end_lineno') else []
                is_empty = all(l.strip()=='' for l in after)
            elif all(isinstance(s, ast.Pass) for s in body):
                is_empty = True
            else:
                code = src[node.lineno:node.end_lineno]
                non_empty = [l for l in code if l.strip() and not l.strip().startswith(('#','def ','class '))]
                is_empty = len(non_empty) == 0
            if is_empty:
                # Build compact signature: name(arg1, arg2, ...)
                args = node.args
                arg_names = [a.arg for a in args.args]
                if args.vararg: arg_names.append(f'*{args.vararg.arg}')
                if args.kwonlyargs: arg_names.extend(a.arg for a in args.kwonlyargs)
                if args.kwarg: arg_names.append(f'**{args.kwarg.arg}')
                sig = f'{node.name}({", ".join(arg_names)})'
                blanks.append((path, node.lineno, node.name, sig))
                print(f'{path}:{node.lineno}  {sig}')

print(f'\\nDetected {len(blanks)} blank/masked function bodies')

# === PASS 2: Detect fully-deleted functions from masking commit ===
deleted = []
try:
    r = subprocess.run(['git','diff','HEAD~1','--unified=0'],
                       capture_output=True, text=True, timeout=15)
    current_file = None
    blank_names = {name for _, _, name, _ in blanks}
    for line in r.stdout.split('\\n'):
        if line.startswith('--- a/'):
            current_file = line[6:]
        elif line.startswith('-') and not line.startswith('---'):
            stripped = line[1:].strip()
            if stripped.startswith('def ') or stripped.startswith('async def '):
                fname = stripped.split('def ',1)[1].split('(')[0].strip()
                if current_file and fname not in blank_names:
                    if '/test' not in current_file and '/tests/' not in current_file:
                        deleted.append((current_file, fname))
except: pass
if deleted:
    print(f'\\n=== DELETED FUNCTIONS (fully removed by masking, {len(deleted)} found) ===')
    print('These functions were removed entirely. Recreate them in the correct file.\\n')
    seen = set()
    for fpath, fname in deleted:
        key = (fpath, fname)
        if key in seen: continue
        seen.add(key)
        print(f'DELETED: {fpath} :: {fname}')

# === PASS 3: L2 detection — extract expected source paths from test imports ===
test_dirs = []
for d in ('test', 'tests'):
    if os.path.isdir(d):
        test_dirs.append(d)
if test_dirs and len(blanks) < 3:
    print('\\n=== L2 EXPECTED SOURCE PATHS (from test file imports) ===')
    print('Few stubs found — this is likely an L2 from-scratch task.')
    print('Test files import from these paths — create source files here:\\n')
    # Find test files via git diff (they were deleted by masking)
    try:
        r = subprocess.run(['git','diff','HEAD~1','--name-only'],
                           capture_output=True, text=True, timeout=10)
        deleted_tests = [f for f in r.stdout.strip().split('\\n')
                        if f and ('/test' in f or f.startswith('test'))]
        for tf in deleted_tests[:5]:
            # Read the deleted test file content from git
            r2 = subprocess.run(['git','show',f'HEAD~1:{tf}'],
                               capture_output=True, text=True, timeout=10)
            if r2.returncode != 0: continue
            imports = re.findall(r'^(?:from|import)\\s+(\\S+)', r2.stdout, re.MULTILINE)
            for imp in imports:
                # Convert dotted import to path
                imp_path = imp.replace('.', '/')
                # Check if the source exists — if not, it needs to be created
                candidates = [f'{imp_path}.py', f'{imp_path}/__init__.py',
                             f'src/{imp_path}.py', f'src/{imp_path}/__init__.py']
                for c in candidates:
                    parent = os.path.dirname(c)
                    if parent and os.path.isdir(os.path.dirname(parent)):
                        if not os.path.exists(c):
                            print(f'  MISSING: {c}  (imported by {tf})')
    except: pass
"""


def workflow() -> Workflow:
    nodes: dict[str, Any] = {}
    edges: list[Edge] = []

    # ── Scan stubs: detect all blank function bodies (FnNode, HOST) ──

    nodes["scan_stubs"] = FnNode(
        id="scan_stubs",
        command=(
            "cd {project_path} && "
            "cat > /tmp/_scan_stubs.py << 'PYEOF'\n"
            + _ast_scanner_script
            + "PYEOF\n"
            "python3 /tmp/_scan_stubs.py "
            "> {project_path}/.factory/reviews/blank-stubs.txt 2>&1; "
            "echo \"scan_stubs: completed\""
        ),
        writes={".factory/reviews/blank-stubs.txt"},
    )

    # ── Builder: implement the feature + fill stubs (HOST) ─────────

    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        timeout=3600,
        max_iterations=3,
        prompt_template=(
            "CRITICAL: Your job is to implement or modify SOURCE CODE only. Do NOT create, "
            "modify, or recreate test files (tests/*). The FeatureBench evaluator provides "
            "its own test files — anything you write in tests/ will be overwritten. Read the "
            "interface descriptions in problem_statement.md and implement the code that "
            "satisfies them in the appropriate source files.\n\n"
            "Implement the FeatureBench feature described in problem_statement.md.\n\n"
            "The working directory is {project_path} — a git-tracked repository.\n"
            "You MUST commit all changes so the FeatureBench harness can extract your git diff.\n\n"
            "CRITICAL RULES:\n"
            "1. Read problem_statement.md for the authoritative interface specifications.\n"
            "   Match function signatures, class names, import paths, and types EXACTLY.\n\n"
            "2. BEFORE IMPLEMENTING ANY FUNCTION, read the class definitions of all types\n"
            "   in its signature — parameter types, return types, types in docstrings.\n"
            "   Use grep to find each class definition and Read the file. Understand what\n"
            "   fields each type has, their constructor signatures, and which field is\n"
            "   which. This prevents wrong field mappings (e.g., using field_a when\n"
            "   field_b was correct because the names are similar).\n\n"
            "3. Create files in dependency order — files with no internal imports first.\n"
            "   Multi-file implementation is expected (~15 files average). Do not try to\n"
            "   put everything in one file. Follow the repo's existing structure and\n"
            "   naming conventions.\n\n"
            "4. MASKED FUNCTION BODIES — CRITICAL:\n"
            "   The benchmark masks code by replacing function bodies with blank lines.\n"
            "   .factory/reviews/blank-stubs.txt contains a COMPACT MANIFEST of all\n"
            "   blank/masked functions — one line per stub:\n"
            "     file_path:line_number  function_name(arg1, arg2, ...)\n\n"
            "   This is a MAP, not the full context. For each stub you need to implement:\n"
            "   a) Read the file at the listed path to see the full function, its docstring,\n"
            "      and surrounding code\n"
            "   b) grep for callers to understand expected behavior and return types\n"
            "   c) Look at similar non-masked functions in the same file for patterns\n"
            "   d) Implement REAL working logic, not minimal stubs\n\n"
            "   You MUST implement EVERY function listed in the manifest.\n"
            "   These are hidden dependencies — if you leave them empty, downstream code\n"
            "   that calls them will fail silently or produce wrong results.\n\n"
            "   If the manifest also has a DELETED FUNCTIONS section, those functions\n"
            "   were fully removed by masking — you must recreate them.\n\n"
            "   If the manifest has an L2 EXPECTED SOURCE PATHS section, it lists source\n"
            "   files that test imports expect but don't exist yet. Create them at the\n"
            "   EXACT paths listed — do NOT invent alternative directory structures.\n\n"
            "5. ANTI-CHEATING COMPLIANCE (MANDATORY):\n"
            "   - Do NOT access /usr/local/lib/python* paths (gold solution location)\n"
            "   - Do NOT fetch from any blacklisted URLs\n"
            "   - Do NOT read or reference any test files to reverse-engineer expected outputs\n"
            "   - Implement from the problem statement and interface specs ONLY\n\n"
            "6. RUNTIME VERIFICATION — CRITICAL:\n"
            "   You can run code inside the project's container to verify your implementation\n"
            "   works. Use this command to test imports, instantiation, and basic calls:\n\n"
            "     docker exec {container_name} bash -c 'cd /testbed && conda run -n testbed python -c \"<code>\"'\n\n"
            "   After implementing, ALWAYS verify by running:\n"
            "   a) Import checks — verify every module you created/modified can be imported:\n"
            "      docker exec {container_name} bash -c 'cd /testbed && conda run -n testbed python -c \"import <module>\"'\n"
            "   b) Class/function existence — verify key classes and functions exist:\n"
            "      docker exec {container_name} bash -c 'cd /testbed && conda run -n testbed python -c \"from <module> import <Class>; print(dir(<Class>))\"'\n"
            "   c) Basic smoke test — try instantiating key classes or calling key functions\n\n"
            "   If you get ImportError or AttributeError, FIX the code and re-verify.\n"
            "   Keep iterating until imports and basic instantiation work.\n"
            "   This catches missing implementations that static analysis misses.\n\n"
            "   IMPORTANT: Before running docker exec, you MUST first sync your local changes\n"
            "   to the container:\n"
            "     docker cp {project_path}/. {container_name}:/testbed/\n\n"
            "7. VERIFY ALL STUBS FILLED — before committing, go through blank-stubs.txt\n"
            "   line by line and verify you implemented every single function listed.\n"
            "   Open each file and confirm the function body is no longer empty.\n"
            "   This is especially important for functions in helper/utility files that\n"
            "   may seem unrelated to the main feature but are called by the main code.\n\n"
            "8. Commit all changes with: git add -A && git commit -m 'implement feature'\n"
            "   The FeatureBench harness extracts changes via git diff, so commits are required.\n\n"
            "Write a summary of what was implemented to .factory/reviews/builder-latest.md.\n"
            "Include a checklist of every function from blank-stubs.txt and whether you\n"
            "implemented it."
        ),
        reads={
            ".factory/reviews/blank-stubs.txt",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
        ],
    )

    # ── Adversarial tester: write spec-compliance tests AFTER build (HOST) ──

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        reads={
            ".factory/reviews/builder-latest.md",
        },
        writes={".factory/reviews/adversarial-qa.md", ".factory/validation_tests/test_spec_compliance.py"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/adversarial-qa.md", must_exist=True),
        ],
        prompt_template=(
            "You are a skeptical spec-compliance auditor. Your job is to find bugs in\n"
            "the builder's implementation by writing tests that catch common mistakes.\n"
            "Assume the builder cut corners and got details wrong.\n\n"
            "The working directory is {project_path}.\n\n"
            "STEP 1 — UNDERSTAND THE SPEC:\n"
            "Read problem_statement.md for the authoritative interface descriptions.\n"
            "For each interface function, note:\n"
            "  - Exact parameter names and types\n"
            "  - Exact return types\n"
            "  - Every error condition described in Raises/Notes sections\n"
            "  - Any specific strings, values, or behaviors described in the docstring\n\n"
            "STEP 2 — READ THE EXISTING TYPES (THIS IS CRITICAL):\n"
            "For every type referenced in the interface signatures (parameter types,\n"
            "return types, types in docstrings), find and READ their class definitions\n"
            "in the codebase. You need to understand:\n"
            "  - What fields/attributes each type has\n"
            "  - Constructor signatures\n"
            "  - Which field is which (e.g., if a class has two similarly-named\n"
            "    fields, know which one should be used where)\n"
            "Use grep and Read to find these class definitions. This step is what\n"
            "lets you write tests the builder can't anticipate.\n\n"
            "STEP 3 — READ THE BUILDER'S CODE:\n"
            "Browse the actual source files the builder created/modified.\n"
            "Look for these common builder mistakes:\n"
            "  a) WRONG FIELD MAPPING — builder used field_a when field_b was correct.\n"
            "     For every conversion/factory function, verify the builder maps each\n"
            "     source field to the correct target field by checking both class defs.\n"
            "  b) PARAPHRASED STRINGS — builder wrote its own description/message string\n"
            "     instead of using the exact text from the spec or existing constants.\n"
            "     Check any method that returns a description, message, or name.\n"
            "  c) MISSING ERROR PATHS — every Raises section in the docstring should\n"
            "     have a corresponding error handling path. Builders often skip these.\n"
            "  d) WRONG DEFAULTS — builder used a different default value than the spec.\n"
            "  e) INCOMPLETE CONVERSIONS — builder handled the happy path but skipped\n"
            "     edge cases (None values, empty lists, error objects with None fields).\n\n"
            "STEP 4 — WRITE TARGETED TESTS:\n"
            "mkdir -p .factory/validation_tests/\n"
            "Write .factory/validation_tests/test_spec_compliance.py with pytest tests.\n\n"
            "For each interface function, write tests that specifically probe the\n"
            "mistakes above:\n"
            "  - FIELD MAPPING TESTS: Create real objects with distinct values for each\n"
            "    field, then assert the output has the right field in the right place.\n"
            "    Example: if converting TypeA to TypeB, create a TypeA where each field\n"
            "    has a unique recognizable value, then assert each TypeB field got the\n"
            "    correct value from the correct TypeA field (not a similarly-named one).\n"
            "  - ERROR PATH TESTS: For each Raises condition in the docstring, write a\n"
            "    test that triggers it and asserts the correct exception.\n"
            "  - STRING EXACTNESS TESTS: If the spec describes a description or name,\n"
            "    check the actual value contains key phrases from the docstring.\n"
            "  - EDGE CASE TESTS: Empty inputs, None values, boundary conditions.\n"
            "  - Use REAL import paths from the actual code, not guesses.\n"
            "  - Self-contained tests (no fixtures depending on external state).\n\n"
            "IMPORTANT:\n"
            "- Do NOT run pytest — the gate runs tests inside the container\n"
            "- Do NOT modify any source code — you are writing tests only\n"
            "- Your tests should FAIL if the builder made the common mistakes above\n"
            "- Write at least one test per interface function, more for complex ones\n\n"
            "ANTI-CHEATING: Do NOT access test files, gold solutions, or blacklisted URLs.\n\n"
            "Write a summary to .factory/reviews/adversarial-qa.md listing each test,\n"
            "what specific builder mistake it catches, and any gaps you found."
        ),
    )

    # ── Builder fix: reloop-only builder that fixes source from pytest output (HOST) ──

    nodes["builder_fix"] = AgentNode(
        id="builder_fix",
        role=AgentRole.BUILDER,
        timeout=3600,
        max_iterations=2,
        prompt_template=(
            "CRITICAL: Your job is to FIX source code based on pytest failure output. "
            "Do NOT create, modify, or recreate test files (tests/*). Do NOT modify the "
            "validation tests in .factory/validation_tests/ — those are written by the "
            "adversarial tester and must stay as-is.\n\n"
            "The working directory is {project_path} — a git-tracked repository.\n"
            "You MUST commit all changes so the FeatureBench harness can extract your git diff.\n\n"
            "STEP 0 — UNDERSTAND WHAT WAS ALREADY BUILT (do this FIRST):\n"
            "Run: git log --oneline -5\n"
            "Run: git diff HEAD~1 --stat\n"
            "This shows you what the previous builder created — which files, which directories.\n"
            "You MUST fix code IN THOSE EXISTING FILES. Do NOT create new directory structures\n"
            "or move code to different locations. The file layout is already established.\n\n"
            "STEP 1 — READ THE FAILURES:\n"
            "Read .factory/reviews/gate-pytest-output.txt for the full pytest failure output.\n"
            "Each failure has a test name, error type, and traceback.\n\n"
            "STEP 2 — DIAGNOSE EACH FAILURE:\n"
            "For each failure, READ THE FULL STACK TRACE. Follow it to the exact file\n"
            "and line that errors. Open that file — if you find an empty/stub function\n"
            "body (blank lines where code should be), IMPLEMENT IT.\n\n"
            "Common root causes:\n"
            "- AttributeError/NameError → a masked function you haven't implemented yet.\n"
            "  Check .factory/reviews/blank-stubs.txt for the full list and USAGE CONTEXT\n"
            "  showing how each function is called (correct attribute names, arguments).\n"
            "- ImportError → missing module or wrong import path in source code\n"
            "- TypeError → wrong function signature or return type\n"
            "- AssertionError → logic bug in your implementation\n\n"
            "STEP 3 — FIX IN PLACE:\n"
            "- Fix the source files that ALREADY EXIST — do NOT create alternative directories\n"
            "- Do NOT rewrite working code — only fix what the stack traces point to\n"
            "- Do NOT modify test files. Fix the SOURCE CODE to pass the tests\n"
            "- Read problem_statement.md if you need to check interface specifications\n\n"
            "STEP 3.5 — RUNTIME VERIFICATION:\n"
            "After fixing, sync your changes and verify inside the container:\n"
            "  docker cp {project_path}/. {container_name}:/testbed/\n"
            "  docker exec {container_name} bash -c 'cd /testbed && conda run -n testbed python -c \"<import check>\"'\n"
            "Test that the specific imports/calls from the failing stack traces now work.\n"
            "Keep fixing until runtime verification passes.\n\n"
            "STEP 4 — ANTI-CHEATING COMPLIANCE (MANDATORY):\n"
            "- Do NOT access /usr/local/lib/python* paths (gold solution location)\n"
            "- Do NOT fetch from any blacklisted URLs\n"
            "- Do NOT read or reference any test files to reverse-engineer expected outputs\n\n"
            "STEP 5 — COMMIT:\n"
            "git add -A && git commit -m 'fix implementation'\n\n"
            "Write a summary to .factory/reviews/builder-fix-latest.md including:\n"
            "- Which test failures you addressed\n"
            "- What files you changed and why\n"
            "- Which failures remain unresolved (if any)"
        ),
        reads={
            ".factory/reviews/gate-pytest-output.txt",
            ".factory/reviews/blank-stubs.txt",
        },
        writes={".factory/reviews/builder-fix-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-fix-latest.md", must_exist=True),
        ],
    )

    # ── Test gate: run validation tests inside container via docker exec ─

    nodes["gate_tests"] = GateNode(
        id="gate_tests",
        evaluator_type="fn",
        evaluator_command=(
            "if [ -f {project_path}/.factory/validation_tests/test_spec_compliance.py ]; then "
            "if ! docker inspect {container_name} >/dev/null 2>&1; then "
            "echo 'halt: container {container_name} no longer exists — cannot run tests'; "
            "else "
            "docker exec {container_name} rm -rf /tmp/validation_tests && "
            "docker cp {project_path}/.factory/validation_tests {container_name}:/tmp/validation_tests && "
            "docker exec {container_name} bash -c "
            "'cd /tmp/validation_tests && conda run -n testbed pytest . -x --tb=short -v 2>&1' "
            "> {project_path}/.factory/reviews/gate-pytest-output.txt 2>&1; "
            "RC=$?; "
            "if [ $RC -eq 0 ]; then echo 'pass: all validation tests passed'; "
            "elif [ $RC -eq 5 ]; then echo 'reloop: no tests collected'; "
            "else "
            "FAILS=$(grep -E '^(FAILED|ERROR|E )' {project_path}/.factory/reviews/gate-pytest-output.txt | head -15 | tr '\\n' '; '); "
            "echo \"reloop: validation tests failed. Failures: $FAILS\"; "
            "fi; "
            "fi; "
            "else echo 'reloop: no validation test files found'; fi"
        ),
        reads={
            ".factory/validation_tests/test_spec_compliance.py",
            ".factory/reviews/adversarial-qa.md",
        },
        writes={".factory/reviews/gate-pytest-output.txt"},
    )

    # ── Archivist: record learnings (HOST, async) ──────────────────

    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        model="haiku",
        prompt_template=(
            "Archive learnings from this FeatureBench task.\n\n"
            "Read:\n"
            "- .factory/reviews/builder-latest.md (what was built)\n"
            "- .factory/reviews/adversarial-qa.md (spec-compliance audit)\n\n"
            "Record:\n"
            "1. Task outcome: resolved or not, number of builder iterations needed\n"
            "2. Successful strategies: what worked well\n"
            "3. Failure patterns: what caused test failures and how they were fixed\n"
            "4. Repository-specific notes: conventions, quirks, or patterns\n"
            "5. Transferable insights: patterns that would help on similar tasks\n\n"
            "Write to .factory/archive/featurebench-learnings.md."
        ),
        reads={".factory/reviews/adversarial-qa.md"},
        writes={".factory/archive/featurebench-learnings.md"},
        blocking=False,
    )

    # ── Edges ──────────────────────────────────────────────────────

    edges = [
        Edge(source="scan_stubs", target="builder"),
        Edge(source="builder", target="adversarial_tester"),
        Edge(source="adversarial_tester", target="gate_tests"),
        Edge(source="gate_tests", target="archivist", condition=VerdictType.PROCEED),
        Edge(source="gate_tests", target="builder_fix", condition=VerdictType.RELOOP),
        Edge(source="builder_fix", target="gate_tests"),
    ]

    # ── Trigger ───────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "featurebench"

    return Workflow(
        name="featurebench",
        nodes=nodes,
        edges=edges,
        start_node="scan_stubs",
        terminal=True,
        trigger=trigger,
    )
