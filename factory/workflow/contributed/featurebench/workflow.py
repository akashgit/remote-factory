"""FeatureBench mode — hybrid host/container execution pipeline.

8-node pipeline with one RELOOP gate:
  researcher → strategist → scan_stubs (FnNode) → builder → adversarial_tester → gate_tests ⇄ builder_fix → archivist

scan_stubs mechanically detects all blank/masked function bodies via AST and
writes the list for the builder. The builder implements the feature AND fills
stubs with full context. After the builder, the adversarial_tester reads the
problem statement AND the built code to write spec-compliance tests (one-shot).
gate_tests runs them inside the container. On RELOOP, builder_fix reads the
pytest output and fixes source code directly — adversarial_tester is NOT
re-run, so the test suite stays stable across iterations.
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
import ast, os, subprocess
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
                blanks.append((path, node.lineno, node.name))
                print(f'{path}:{node.lineno} {node.name}')

print(f'\\nDetected {len(blanks)} blank/masked function bodies')
print('\\n=== USAGE CONTEXT (how each blank function is called) ===')
for path, lineno, name in blanks:
    try:
        r = subprocess.run(['grep','-rn','--include=*.py',f'.{name}','.'],
                           capture_output=True, text=True, timeout=5)
        usages = []
        for line in r.stdout.split('\\n'):
            if not line.strip(): continue
            if f'{path}:{lineno}' in line: continue
            if 'def ' + name in line: continue
            if '.factory/' in line: continue
            usages.append(line)
            if len(usages) >= 3: break
        if usages:
            print(f'\\n{path}:{lineno} {name}')
            for u in usages:
                print(f'  CALLED: {u}')
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
            "4. For L2 (from-scratch) tasks:\n"
            "   - The repo may be nearly empty — plan the full project structure\n"
            "   - Note any README.md or configuration files that hint at expected structure\n\n"
            "5. Summarize:\n"
            "   - Files that need to be created (with exact paths from interface specs)\n"
            "   - Files that need to be modified\n"
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
            "Produce a plan with:\n\n"
            "1. **File Creation Order** — list every file to create, in dependency order\n"
            "   (files with no internal deps first, files that import from them later).\n"
            "   For each file: exact path, what it implements, which interface spec it satisfies.\n\n"
            "2. **File Modification Plan** — for each existing file that needs changes:\n"
            "   what to add/modify and why.\n\n"
            "3. **Interface Compliance Checklist** — for each interface spec in the problem\n"
            "   statement, list: the file, the function/class signature, the expected behavior.\n"
            "   The builder MUST match these exactly.\n\n"
            "4. **Test Strategy** — which F2P tests validate which features.\n\n"
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
            "   .factory/reviews/blank-stubs.txt contains:\n"
            "   - A list of ALL blank/masked functions (file:line name)\n"
            "   - A USAGE CONTEXT section showing how each function is called elsewhere\n\n"
            "   You MUST read this file and implement EVERY function listed there.\n"
            "   These are hidden dependencies — if you leave them empty, downstream code\n"
            "   that calls them will fail silently or produce wrong results.\n\n"
            "   The USAGE CONTEXT section is critical for getting names right:\n"
            "   - It shows how callers reference attributes and methods\n"
            "   - Use the EXACT attribute names shown in the usage (e.g., if callers\n"
            "     use self._precision_input, name your attribute _precision_input)\n"
            "   - Match return types and signatures to what callers expect\n\n"
            "   For each masked function:\n"
            "   - Check the USAGE CONTEXT to see how it is called\n"
            "   - Read the docstring for behavioral hints\n"
            "   - Look at similar non-masked functions for patterns\n"
            "   - Implement REAL working logic, not minimal stubs\n\n"
            "5. ANTI-CHEATING COMPLIANCE (MANDATORY):\n"
            "   - Do NOT access /usr/local/lib/python* paths (gold solution location)\n"
            "   - Do NOT fetch from any blacklisted URLs\n"
            "   - Do NOT read or reference any test files to reverse-engineer expected outputs\n"
            "   - Implement from the problem statement and interface specs ONLY\n\n"
            "6. VERIFY ALL STUBS FILLED — before committing, go through blank-stubs.txt\n"
            "   line by line and verify you implemented every single function listed.\n"
            "   Open each file and confirm the function body is no longer empty.\n"
            "   This is especially important for functions in helper/utility files that\n"
            "   may seem unrelated to the main feature but are called by the main code.\n\n"
            "7. Commit all changes with: git add -A && git commit -m 'implement feature'\n"
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
            "INSTRUCTIONS:\n"
            "1. Read .factory/reviews/gate-pytest-output.txt for the pytest failure output.\n"
            "   Each failure has a test name, error type, and traceback.\n\n"
            "2. For each failure, READ THE FULL STACK TRACE. Follow it to the exact file\n"
            "   and line that errors. Open that file — if you find an empty/stub function\n"
            "   body (blank lines where code should be), IMPLEMENT IT.\n\n"
            "3. Common root causes:\n"
            "   - AttributeError/NameError → a masked function you haven't implemented yet.\n"
            "     Check .factory/reviews/blank-stubs.txt for the full list and USAGE CONTEXT\n"
            "     showing how each function is called (correct attribute names, arguments).\n"
            "   - ImportError → missing module or wrong import path in source code\n"
            "   - TypeError → wrong function signature or return type\n"
            "   - AssertionError → logic bug in your implementation\n\n"
            "4. Do NOT rewrite working code — only fix what the stack traces point to.\n"
            "   Do NOT modify test files. Fix the SOURCE CODE to pass the tests.\n\n"
            "5. Read problem_statement.md if you need to check interface specifications.\n"
            "   Read .factory/reviews/blank-stubs.txt for the usage context of any\n"
            "   masked function — it shows how callers reference attributes and methods.\n\n"
            "6. ANTI-CHEATING COMPLIANCE (MANDATORY):\n"
            "   - Do NOT access /usr/local/lib/python* paths (gold solution location)\n"
            "   - Do NOT fetch from any blacklisted URLs\n"
            "   - Do NOT read or reference any test files to reverse-engineer expected outputs\n\n"
            "7. Commit all changes with: git add -A && git commit -m 'fix implementation'\n\n"
            "Write a summary of what was fixed to .factory/reviews/builder-latest.md."
        ),
        reads={
            ".factory/reviews/gate-pytest-output.txt",
            ".factory/strategy/current.md",
            ".factory/reviews/blank-stubs.txt",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(path=".factory/reviews/builder-latest.md", must_exist=True),
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
            "else echo 'reloop: validation tests failed — see .factory/reviews/gate-pytest-output.txt'; fi; "
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
        start_node="researcher",
        terminal=True,
        trigger=trigger,
    )
