"""FeatureBench mode — hybrid host/container execution pipeline.

10-node pipeline with two RELOOP gates:
  researcher → strategist → scan_stubs (FnNode) → builder → qa_reviewer → gate_qa ⇄ builder
  gate_qa (PROCEED) → adversarial_tester → gate_tests ⇄ builder_fix → archivist

scan_stubs mechanically detects all blank/masked function bodies via AST and
writes the list for the builder. The builder implements the feature AND fills
stubs with full context. After the builder, qa_reviewer checks for consistency
issues (naming, stubs, imports). gate_qa reads the QA report — if remaining
unfixed issues are found, it reloops to builder. Otherwise,
adversarial_tester reads the problem statement AND the built code to write
spec-compliance tests (one-shot). gate_tests runs them inside the container.
On RELOOP, builder_fix reads the pytest output and fixes source code directly
— adversarial_tester is NOT re-run, so the test suite stays stable across
iterations.
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
        "FeatureBench mode — TDD pipeline for implementing features from interface specifications. "
        "Reads problem_statement.md, analyzes repo structure, creates an implementation plan, "
        "scans for masked function bodies, builds the feature, then the adversarial_tester "
        "writes spec-compliance tests against the built code. gate_tests runs them inside the "
        "container via docker exec, RELOOPing to builder if they fail. "
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

    # ── Researcher: analyze problem + repo (HOST) ─────────────────

    nodes["researcher"] = AgentNode(
        id="researcher",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze the FeatureBench problem statement and repository structure.\n\n"
            "Note: the test files have been removed by the benchmark harness. Focus on "
            "understanding what source code needs to be written/modified to implement the "
            "described interface. Do NOT plan to recreate test files.\n\n"
            "The problem statement is at {project_path}/problem_statement.md.\n"
            "Repository files are available at {project_path} for analysis.\n\n"
            "1. Read problem_statement.md thoroughly. Extract:\n"
            "   - Core Functionality overview\n"
            "   - Main Features and Requirements (enumerate each)\n"
            "   - Key Challenges (mandatory components)\n"
            "   - Interface Descriptions (file paths, import paths, function signatures, types)\n\n"
            "2. Study the existing repository structure:\n"
            "   - List all source files and their purposes\n"
            "   - Identify which modules/packages exist\n"
            "   - Map import dependencies between files\n"
            "   - Note any existing test infrastructure\n\n"
            "3. For L1 (incremental) tasks:\n"
            "   - Identify the extension points in the existing codebase\n"
            "   - Map where new code must integrate with existing code\n"
            "   - Note existing patterns (naming, error handling, logging) to follow\n"
            "   - CRITICAL: The benchmark masks code by replacing function bodies with blank\n"
            "     lines. Scan ALL files referenced by the interface specs for empty/stub\n"
            "     function bodies — these are hidden dependencies you must also implement.\n"
            "     Trace the call chain from each interface function to find masked helpers\n"
            "     in other files (models, utilities, threading helpers, etc.).\n\n"
            "4. For L2 (from-scratch) tasks — THIS IS CRITICAL:\n"
            "   - The benchmark DELETED the target source files, but the REST of the repo\n"
            "     is intact. You have a full working codebase to study as a template.\n"
            "   - FIND SIBLING IMPLEMENTATIONS in the same directory tree. For example,\n"
            "     if you need to implement src/transformers/models/seggpt/, look at\n"
            "     src/transformers/models/bert/ or src/transformers/models/vit/ as templates.\n"
            "   - Run: ls on the parent directory to find all siblings.\n"
            "   - Pick 1-2 siblings that are closest in functionality to the target.\n"
            "   - For each sibling, document the EXACT file structure:\n"
            "     * Which files exist (modeling_X.py, configuration_X.py, __init__.py, etc.)\n"
            "     * What classes/functions each file defines (with signatures)\n"
            "     * How __init__.py exports symbols\n"
            "     * How the module integrates with the parent package (registration, imports)\n"
            "   - Report this as a STRUCTURAL TEMPLATE section that the builder MUST follow.\n"
            "   - Also check the parent package's __init__.py for registration patterns\n"
            "     (e.g., model mappings, auto-classes, lazy imports).\n"
            "   - Look at any configuration files, setup.py, or pyproject.toml for patterns.\n\n"
            "5. Summarize:\n"
            "   - Files that need to be created (with EXACT paths from interface specs)\n"
            "   - Files that need to be modified\n"
            "   - Structural template from sibling implementation (for L2)\n"
            "   - Integration points and dependency order\n"
            "   - Potential challenges or ambiguities in the spec\n\n"
            "Write findings to .factory/reviews/researcher-latest.md."
        ),
        reads=set(),
        writes={".factory/reviews/researcher-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/researcher-latest.md", must_exist=True),
        ],
    )

    # ── Strategist: create implementation plan (HOST) ──────────────

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Create an implementation plan for this FeatureBench task.\n\n"
            "Note: the test files have been removed by the benchmark harness. Focus on "
            "understanding what source code needs to be written/modified to implement the "
            "described interface. Do NOT plan to recreate test files.\n\n"
            "The problem statement is at {project_path}/problem_statement.md.\n"
            "All file paths in your plan must be relative to the project root.\n\n"
            "Read the researcher's analysis at .factory/reviews/researcher-latest.md.\n"
            "Read the problem statement at problem_statement.md.\n\n"
            "IMPORTANT: Do NOT rely solely on the researcher's report. Browse the actual\n"
            "code at {project_path} to verify paths, naming conventions, and integration\n"
            "points. For L2 tasks where source files were deleted, examine sibling\n"
            "implementations directly — ls the parent directory, open 1-2 siblings, and\n"
            "use their file structure as a concrete template for your plan.\n\n"
            "Produce a plan with:\n\n"
            "1. **File Creation Order** — list EVERY file to create, in dependency order\n"
            "   (files with no internal deps first, files that import from them later).\n"
            "   For each file: EXACT path (verified against repo structure), what it\n"
            "   implements, which interface spec it satisfies, and which sibling file to\n"
            "   use as a template (if applicable).\n\n"
            "2. **File Modification Plan** — for each existing file that needs changes:\n"
            "   what to add/modify and why. Check the actual file content to confirm.\n\n"
            "3. **Structural Template** (for L2 tasks) — document the sibling implementation\n"
            "   structure the builder should follow. Include exact file names, class names,\n"
            "   and the pattern for __init__.py exports and parent package registration.\n\n"
            "4. **Interface Compliance Checklist** — for each interface spec in the problem\n"
            "   statement, list: the file, the function/class signature, the expected behavior.\n"
            "   The builder MUST match these exactly.\n\n"
            "5. **Risk Areas** — parts of the spec that are ambiguous or could cause P2P\n"
            "   regressions.\n\n"
            "Write the plan to .factory/strategy/current.md."
        ),
        reads={".factory/reviews/researcher-latest.md"},
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(path=".factory/strategy/current.md", must_exist=True),
        ],
    )

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
            "Implement the FeatureBench feature according to the plan at "
            ".factory/strategy/current.md.\n\n"
            "The working directory is {project_path} — a git-tracked repository.\n"
            "You MUST commit all changes so the FeatureBench harness can extract your git diff.\n\n"
            "CRITICAL RULES:\n"
            "1. Read problem_statement.md for the authoritative interface specifications.\n"
            "   Match function signatures, class names, import paths, and types EXACTLY.\n\n"
            "2. Follow the file creation order from the plan. Create files in dependency\n"
            "   order — files with no internal imports first.\n\n"
            "3. Multi-file implementation is expected (~15 files average). Do not try to\n"
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
            ".factory/strategy/current.md",
            ".factory/reviews/blank-stubs.txt",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
        ],
    )

    # ── QA reviewer: check builder output for consistency issues (HOST) ──

    nodes["qa_reviewer"] = AgentNode(
        id="qa_reviewer",
        role=AgentRole.CODE_REVIEWER,
        timeout=1200,
        prompt_template=(
            "You are a code consistency reviewer. The builder just implemented a feature.\n"
            "Your job is to find and FIX internal inconsistencies in the code before\n"
            "tests are written against it.\n\n"
            "The working directory is {project_path}.\n\n"
            "STEP 1 — Read .factory/reviews/builder-latest.md to see what was built.\n"
            "Read .factory/reviews/blank-stubs.txt for the list of masked functions.\n\n"
            "STEP 2 — For each file the builder modified or created, check:\n\n"
            "  a) NAMING CONSISTENCY: Look at the file's own imports and type annotations.\n"
            "     Do the internal attribute/variable names follow the vocabulary of the\n"
            "     types imported in THAT file? For example, if a file imports a type\n"
            "     called _PRECISION_INPUT, attributes storing that type should use\n"
            "     'input' in their name (e.g., _precision_input), not a different word\n"
            "     like 'flag' copied from a different file. Sibling/parallel\n"
            "     implementations in other files often use different internal names —\n"
            "     the file's own imports are the authoritative naming signal.\n\n"
            "  b) STUB COMPLETENESS: blank-stubs.txt is a compact manifest — one line\n"
            "     per stub (file:line  function_name(args)). Open each listed file and\n"
            "     verify the function body is no longer empty. Flag any that are still\n"
            "     stubs or contain only pass/raise NotImplementedError.\n\n"
            "  c) INTERFACE COMPLIANCE: Read problem_statement.md for the interface specs.\n"
            "     Verify that public attribute names, method signatures, and return types\n"
            "     match the spec exactly. Check that the class exposes the attributes\n"
            "     and methods the spec describes.\n\n"
            "  d) CROSS-FILE CONSISTENCY: If the builder's code sets attributes that are\n"
            "     accessed by callers in other (non-modified) files, verify the names\n"
            "     match. grep for how other files reference the class and its attributes.\n\n"
            "  e) IMPORT COHERENCE: Verify that modified files import everything they use\n"
            "     and do not break existing imports that other files depend on.\n\n"
            "  f) RUNTIME SMOKE TEST: Sync code to the container and verify key imports work:\n"
            "     docker cp {project_path}/. {container_name}:/testbed/\n"
            "     docker exec {container_name} bash -c 'cd /testbed && conda run -n testbed python -c \"from <module> import <Class>\"'\n"
            "     Test at least the main modules referenced in problem_statement.md.\n"
            "     If imports fail (ImportError, AttributeError), that reveals missing code.\n\n"
            "STEP 3 — If you find issues, FIX them in the source files.\n"
            "  Do NOT modify test files. Only fix source code.\n"
            "  After fixing, run: git add -A && git commit -m 'fix consistency issues'\n\n"
            "STEP 4 — Write a brief report to .factory/reviews/qa-review-latest.md:\n"
            "  - Issues found and fixed (with before/after)\n"
            "  - Remaining stubs that still need implementation\n"
            "  - 'No issues found' if everything is consistent\n\n"
            "STEP 5 — VERDICT (MANDATORY — must be the LAST line of the report):\n"
            "  If all issues are resolved or no issues were found, write exactly:\n"
            "    VERDICT: PASS\n"
            "  If there are remaining unfixed issues (stubs still empty, naming\n"
            "  problems you could not resolve, broken imports), write exactly:\n"
            "    VERDICT: FAIL — <one-line description of remaining issues>\n"
            "  The VERDICT line MUST be the very last line of the report.\n\n"
            "ANTI-CHEATING: Do NOT access test files, gold solutions, or blacklisted URLs."
        ),
        reads={
            ".factory/reviews/builder-latest.md",
            ".factory/reviews/blank-stubs.txt",
        },
        writes={".factory/reviews/qa-review-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/qa-review-latest.md", must_exist=True),
        ],
    )

    # ── QA gate: reloop to builder if QA found remaining issues ──

    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="fn",
        evaluator_command=(
            "QA_REPORT={project_path}/.factory/reviews/qa-review-latest.md; "
            "if [ ! -f \"$QA_REPORT\" ]; then echo 'reloop: QA report missing'; "
            "else "
            "VERDICT=$(tail -5 \"$QA_REPORT\" | grep -i '^VERDICT:' | tail -1); "
            "if echo \"$VERDICT\" | grep -qi 'PASS'; then echo 'pass: QA review passed'; "
            "elif echo \"$VERDICT\" | grep -qi 'FAIL'; then "
            "echo \"reloop: $VERDICT\"; "
            "else echo 'pass: no explicit verdict, assuming clean'; "
            "fi; fi"
        ),
        reads={".factory/reviews/qa-review-latest.md"},
        writes=set(),
    )

    # ── Adversarial tester: write spec-compliance tests AFTER build (HOST) ──

    nodes["adversarial_tester"] = AgentNode(
        id="adversarial_tester",
        role=AgentRole.ADVERSARIAL_TESTER,
        timeout=1800,
        reads={
            ".factory/reviews/researcher-latest.md",
            ".factory/reviews/builder-latest.md",
        },
        writes={".factory/reviews/adversarial-qa.md", ".factory/validation_tests/test_spec_compliance.py"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/adversarial-qa.md", must_exist=True),
        ],
        prompt_template=(
            "You are a spec-compliance auditor for the FeatureBench benchmark.\n\n"
            "The builder has ALREADY implemented the feature. Your job is to compare\n"
            "the problem statement against the built code and write tests that verify\n"
            "the implementation matches the spec.\n\n"
            "Steps:\n"
            "1. Read problem_statement.md for the authoritative interface specs\n"
            "   (function signatures, import paths, types, expected behavior).\n"
            "2. Read .factory/reviews/builder-latest.md for what was built.\n"
            "3. Read .factory/reviews/researcher-latest.md for repo structure.\n"
            "4. CRITICALLY: Browse the ACTUAL source files the builder created/modified.\n"
            "   Find the real import paths, class names, and function signatures.\n"
            "   Your tests MUST use the actual imports that exist in the code.\n\n"
            "5. mkdir -p .factory/validation_tests/\n"
            "6. Write .factory/validation_tests/test_spec_compliance.py with pytest tests\n"
            "   that verify EVERY interface spec from problem_statement.md:\n"
            "   - Use the REAL import paths from the built code (not guesses)\n"
            "   - test_ prefix for all test functions\n"
            "   - Function signature tests (correct parameters, return types)\n"
            "   - Happy-path tests for each specified interface\n"
            "   - Edge-case tests (empty input, None, boundary values)\n"
            "   - Tests for spec requirements the builder may have missed\n"
            "   - Self-contained tests (no fixtures depending on external state)\n"
            "   - Do NOT reference test data files unless you verify they exist\n\n"
            "IMPORTANT:\n"
            "- Do NOT run pytest — the gate runs tests inside the container\n"
            "- Do NOT modify any source code — you are writing tests only\n"
            "- Use REAL import paths from the actual code, not from the spec description\n"
            "  (the spec describes interfaces but the actual module paths may differ)\n\n"
            "Write a summary to .factory/reviews/adversarial-qa.md listing the tests\n"
            "written, what spec requirement each validates, and any gaps you found\n"
            "between the spec and the implementation."
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
            ".factory/strategy/current.md",
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
            "- .factory/strategy/current.md (implementation plan)\n"
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
        Edge(source="researcher", target="strategist"),
        Edge(source="strategist", target="scan_stubs"),
        Edge(source="scan_stubs", target="builder"),
        Edge(source="builder", target="qa_reviewer"),
        Edge(source="qa_reviewer", target="gate_qa"),
        Edge(source="gate_qa", target="adversarial_tester", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
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
        start_node="researcher",
        terminal=True,
        trigger=trigger,
    )
