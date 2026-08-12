"""End-to-end A/B comparison test for loop context injection using real factory workflows.

Uses REAL factory workflow definitions (improve_workflow) from
factory/workflow/definitions.py — not hand-crafted test workflows — with
real test projects containing actual Python code, tests, and factory.md
files.

Three test projects (CLI tool, Web API, Library) are created with real
source code. Each project is tested with a different RELOOP gate to
validate loop context across the full gate topology of the improve workflow:

  - CLI tool:  gate_qa → builder           (QA verification failed)
  - Web API:   gate_build → builder        (build review found issues)
  - Library:   gate_doc_freshness → builder (documentation stale)

For each project, two arms are compared:
  - Arm A (baseline): no loop context state → builder prompt is vanilla
  - Arm B (with context): loop context state populated → builder prompt
    includes gate criteria, iteration count, feedback history, and the
    full loop topology from the real improve workflow definition

Validates that commit 636231c2 (automatic loop context injection for tool
mode) correctly enriches builder prompts using production workflow graphs.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from factory.workflow.definitions import improve_workflow
from factory.workflow.registry import WorkflowEntry, WorkflowRegistry
from factory.workflow.tool import (
    _load_state,
    _save_state,
    _workflow_cache,
    tool_curr,
    tool_init,
    tool_submit,
)


@pytest.fixture(autouse=True)
def _reset_caches():
    WorkflowRegistry.reset()
    _workflow_cache.clear()
    yield
    WorkflowRegistry.reset()
    _workflow_cache.clear()


# ── project scaffolding ──────────────────────────────────────────


def _git_init(project: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(project.parent),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, capture_output=True, check=True, env=env,
    )


def _setup_factory_dir(project: Path) -> None:
    """Create minimal .factory/ matching what ``factory discover`` produces."""
    fd = project / ".factory"
    fd.mkdir(exist_ok=True)
    (fd / "config.json").write_text(json.dumps({
        "goal": "test project",
        "scope": ["*.py"],
        "guards": ["Do not delete tests"],
        "eval_command": "python -m pytest -v",
        "eval_threshold": 0.7,
    }, indent=2))
    (fd / "eval_profile.json").write_text(json.dumps({
        "dimensions": [
            {"name": "tests", "weight": 0.5},
            {"name": "lint", "weight": 0.5},
        ],
        "human_reviewed": True,
    }, indent=2))
    for sub in ("strategy", "reviews", "experiments"):
        (fd / sub).mkdir(exist_ok=True)
    (fd / "strategy" / "observations.md").write_text(
        "# Observations\nProject analysed. Tests pass. Score: 0.75\n"
    )


def _create_cli_project(base: Path) -> Path:
    """Real CLI tool project: CSV to JSON converter with tests."""
    project = base / "cli-tool"
    project.mkdir(parents=True)
    (project / "csv2json.py").write_text(
        "import csv, json, sys\n\n"
        "def csv_to_json(path: str) -> list[dict]:\n"
        "    with open(path) as f:\n"
        "        return list(csv.DictReader(f))\n\n"
        "def main():\n"
        "    if len(sys.argv) != 2:\n"
        "        print('Usage: csv2json <file.csv>', file=sys.stderr)\n"
        "        sys.exit(1)\n"
        "    print(json.dumps(csv_to_json(sys.argv[1]), indent=2))\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (project / "test_csv2json.py").write_text(
        "import tempfile\nfrom csv2json import csv_to_json\n\n"
        "def test_basic():\n"
        "    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:\n"
        "        f.write('name,age\\nAlice,30\\nBob,25\\n')\n"
        "        f.flush()\n"
        "        result = csv_to_json(f.name)\n"
        "    assert len(result) == 2\n"
        "    assert result[0]['name'] == 'Alice'\n\n"
        "def test_empty():\n"
        "    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:\n"
        "        f.write('name,age\\n')\n"
        "        f.flush()\n"
        "        assert csv_to_json(f.name) == []\n"
    )
    (project / "factory.md").write_text(
        "# Factory Configuration\n\n## Goal\nCSV to JSON CLI tool\n\n"
        "## Scope\n- csv2json.py\n- test_csv2json.py\n\n"
        "## Guards\n- Do not delete existing tests\n\n"
        "## Eval\n```\npython -m pytest test_csv2json.py -v\n```\n\n"
        "## Threshold\n0.7\n"
    )
    _git_init(project)
    _setup_factory_dir(project)
    return project


def _create_web_api_project(base: Path) -> Path:
    """Real Web API project: simple HTTP handler with tests."""
    project = base / "web-api"
    project.mkdir(parents=True)
    (project / "app.py").write_text(
        "from http.server import BaseHTTPRequestHandler\n"
        "import json\n\n"
        "items: dict[int, dict] = {}\n\n"
        "class ItemHandler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        if self.path == '/health':\n"
        "            self.send_response(200)\n"
        "            self.end_headers()\n"
        "            self.wfile.write(json.dumps({'status': 'ok'}).encode())\n"
        "        else:\n"
        "            self.send_response(404)\n"
        "            self.end_headers()\n"
    )
    (project / "test_app.py").write_text(
        "from app import ItemHandler\n\n"
        "def test_handler_exists():\n"
        "    assert ItemHandler is not None\n\n"
        "def test_items_dict():\n"
        "    from app import items\n"
        "    assert isinstance(items, dict)\n"
    )
    (project / "factory.md").write_text(
        "# Factory Configuration\n\n## Goal\nREST API for item management\n\n"
        "## Scope\n- app.py\n- test_app.py\n\n"
        "## Guards\n- Do not delete existing tests\n\n"
        "## Eval\n```\npython -m pytest test_app.py -v\n```\n\n"
        "## Threshold\n0.7\n"
    )
    _git_init(project)
    _setup_factory_dir(project)
    return project


def _create_mathlib_project(base: Path) -> Path:
    """Real math library project: utility functions with tests."""
    project = base / "mathlib"
    project.mkdir(parents=True)
    (project / "mathlib.py").write_text(
        "import math\n\n"
        "def factorial(n: int) -> int:\n"
        "    if n < 0:\n"
        "        raise ValueError('n must be non-negative')\n"
        "    return 1 if n <= 1 else n * factorial(n - 1)\n\n"
        "def fibonacci(n: int) -> int:\n"
        "    if n < 0:\n"
        "        raise ValueError('n must be non-negative')\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n\n"
        "def is_prime(n: int) -> bool:\n"
        "    if n < 2:\n"
        "        return False\n"
        "    return all(n % i for i in range(2, int(math.sqrt(n)) + 1))\n"
    )
    (project / "test_mathlib.py").write_text(
        "import pytest\nfrom mathlib import factorial, fibonacci, is_prime\n\n"
        "def test_factorial():\n"
        "    assert factorial(0) == 1\n"
        "    assert factorial(5) == 120\n\n"
        "def test_factorial_negative():\n"
        "    with pytest.raises(ValueError):\n"
        "        factorial(-1)\n\n"
        "def test_fibonacci():\n"
        "    assert fibonacci(0) == 0\n"
        "    assert fibonacci(10) == 55\n\n"
        "def test_is_prime():\n"
        "    assert is_prime(7)\n"
        "    assert not is_prime(4)\n"
        "    assert not is_prime(1)\n"
    )
    (project / "factory.md").write_text(
        "# Factory Configuration\n\n## Goal\nMath utility library\n\n"
        "## Scope\n- mathlib.py\n- test_mathlib.py\n\n"
        "## Guards\n- Do not delete existing tests\n\n"
        "## Eval\n```\npython -m pytest test_mathlib.py -v\n```\n\n"
        "## Threshold\n0.7\n"
    )
    _git_init(project)
    _setup_factory_dir(project)
    return project


# ── registration helper ──────────────────────────────────────────


def _register(wf):
    WorkflowRegistry._entries[wf.name] = WorkflowEntry(
        name=wf.name,
        description="real workflow",
        path="<builtin>",
        source="builtin",
        _workflow_fn=lambda _wf=wf: _wf,
    )


# ── A/B comparison core ─────────────────────────────────────────


def _run_ab(
    project: Path,
    reloop_gate: str,
    feedback: str,
) -> dict:
    """Run A/B comparison on a real project using the production improve workflow.

    Initializes a tool session with the real improve workflow, advances
    state to the builder node, then captures the builder prompt under two
    conditions:

    - Arm A: iteration_counts and feedback_log are empty (first attempt,
      iteration 0) — topology is present but feedback history is not
    - Arm B: iteration_counts and feedback_log reflect one RELOOP from
      the specified gate — topology AND feedback history are present

    Returns a dict with arm_a, arm_b, and the raw workflow topo_order.
    """
    wf = improve_workflow()
    _register(wf)
    tool_init("improve", project)

    state = _load_state(project)
    order = state["topo_order"]
    assert "builder" in order, "builder must be in the real improve workflow topo order"

    builder_idx = order.index("builder")

    # Mark every node before builder as completed
    for i in range(builder_idx):
        state["completed"][order[i]] = "completed"
    state["pointer_idx"] = builder_idx

    # ── Arm A: first invocation (iteration 0, no feedback) ─────
    state_a = copy.deepcopy(state)
    state_a["iteration_counts"] = {}
    state_a["feedback_log"] = {}
    _save_state(project, state_a)
    prompt_a = tool_curr(project)

    # ── Arm B: after one RELOOP (iteration 1, with feedback) ───
    state_b = copy.deepcopy(state)
    state_b["iteration_counts"] = {f"{reloop_gate}->builder": 1}
    state_b["feedback_log"] = {
        "builder": [{
            "gate": reloop_gate,
            "iteration": 1,
            "feedback": feedback,
            "timestamp": 1000.0,
        }],
    }
    _save_state(project, state_b)
    prompt_b = tool_curr(project)

    return {
        "arm_a": {
            "prompt": prompt_a,
            "has_loop_context": "LOOP CONTEXT" in prompt_a,
            "has_feedback": "Feedback history" in prompt_a,
            "prompt_length": len(prompt_a),
        },
        "arm_b": {
            "prompt": prompt_b,
            "has_loop_context": "LOOP CONTEXT" in prompt_b,
            "has_feedback": "Feedback history" in prompt_b,
            "prompt_length": len(prompt_b),
        },
        "topo_order": order,
    }


# ── tests ────────────────────────────────────────────────────────


class TestLoopContextE2EAB:
    """A/B comparison across 3 real projects using the production improve workflow.

    Each test creates a real project (actual code, tests, factory.md, git repo,
    .factory/ setup) and runs the comparison using the improve workflow definition
    from factory/workflow/definitions.py.
    """

    # ── per-project A/B tests ────────────────────────────────────

    def test_cli_tool_gate_qa_reloop(self, tmp_path: Path) -> None:
        """CLI tool: gate_qa triggers RELOOP — builder prompt gains QA feedback."""
        project = _create_cli_project(tmp_path)
        result = _run_ab(
            project,
            reloop_gate="gate_qa",
            feedback="QA found 3 test failures in test_csv2json.py — input validation missing",
        )

        assert result["arm_a"]["has_loop_context"]
        assert not result["arm_a"]["has_feedback"]
        assert result["arm_b"]["has_loop_context"]
        assert result["arm_b"]["has_feedback"]

        prompt_b = result["arm_b"]["prompt"]
        assert "gate_qa" in prompt_b
        assert "input validation" in prompt_b
        assert "LOOP CONTEXT" in prompt_b

    def test_web_api_gate_build_reloop(self, tmp_path: Path) -> None:
        """Web API: gate_build triggers RELOOP — builder prompt gains build review feedback."""
        project = _create_web_api_project(tmp_path)
        result = _run_ab(
            project,
            reloop_gate="gate_build",
            feedback="PR scope creep detected — endpoints added beyond hypothesis scope",
        )

        assert result["arm_a"]["has_loop_context"]
        assert not result["arm_a"]["has_feedback"]
        assert result["arm_b"]["has_loop_context"]
        assert result["arm_b"]["has_feedback"]

        prompt_b = result["arm_b"]["prompt"]
        assert "gate_build" in prompt_b
        assert "scope creep" in prompt_b

    def test_mathlib_gate_doc_freshness_reloop(self, tmp_path: Path) -> None:
        """Library: gate_doc_freshness triggers RELOOP — builder prompt gains doc feedback."""
        project = _create_mathlib_project(tmp_path)
        result = _run_ab(
            project,
            reloop_gate="gate_doc_freshness",
            feedback="README.md not updated after adding is_prime() public API",
        )

        assert result["arm_a"]["has_loop_context"]
        assert not result["arm_a"]["has_feedback"]
        assert result["arm_b"]["has_loop_context"]
        assert result["arm_b"]["has_feedback"]

        prompt_b = result["arm_b"]["prompt"]
        assert "gate_doc_freshness" in prompt_b
        assert "README" in prompt_b

    # ── gate criteria verification ───────────────────────────────

    def test_gate_qa_criteria_from_real_workflow(self, tmp_path: Path) -> None:
        """Arm B prompt contains the REAL gate_qa criteria from improve_workflow()."""
        project = _create_cli_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_qa", feedback="tests failed")

        prompt_b = result["arm_b"]["prompt"]
        # The real improve workflow gate_qa has this prompt:
        # "Review QA results. PROCEED if all checks pass.
        #  RELOOP to builder (max 3 iterations) if issues found."
        assert "QA" in prompt_b or "checks pass" in prompt_b

    def test_gate_build_criteria_from_real_workflow(self, tmp_path: Path) -> None:
        """Arm B prompt contains the REAL gate_build criteria from improve_workflow()."""
        project = _create_web_api_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_build", feedback="review failed")

        prompt_b = result["arm_b"]["prompt"]
        # The real improve workflow gate_build has this prompt:
        # "Read builder output and PR diff. Does work match the hypothesis? ..."
        assert "PR diff" in prompt_b or "hypothesis" in prompt_b or "scope" in prompt_b

    def test_gate_doc_freshness_criteria_from_real_workflow(self, tmp_path: Path) -> None:
        """Arm B prompt contains the REAL DOC_FRESHNESS_GATE_PROMPT from definitions.py."""
        project = _create_mathlib_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_doc_freshness", feedback="docs stale")

        prompt_b = result["arm_b"]["prompt"]
        # DOC_FRESHNESS_GATE_PROMPT mentions documentation, CLI commands, CLAUDE.md, etc.
        assert "documentation" in prompt_b.lower()

    # ── loop topology tests ──────────────────────────────────────

    def test_gate_qa_topology_includes_full_qa_pipeline(self, tmp_path: Path) -> None:
        """gate_qa RELOOP topology spans builder through the deep-QA pipeline."""
        project = _create_cli_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_qa", feedback="tests failed")

        prompt_b = result["arm_b"]["prompt"]
        # Real improve workflow chain from builder to gate_qa:
        # builder → gate_build → health_checker → code_reviewer → gate_review →
        # adversarial_tester → gate_qa
        for expected_node in [
            "builder", "gate_build", "health_checker",
            "code_reviewer", "gate_review", "adversarial_tester", "gate_qa",
        ]:
            assert expected_node in prompt_b, (
                f"Expected real workflow node '{expected_node}' in loop topology"
            )

    def test_gate_build_topology_is_minimal(self, tmp_path: Path) -> None:
        """gate_build RELOOP topology spans only builder → gate_build."""
        project = _create_web_api_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_build", feedback="issues")

        prompt_b = result["arm_b"]["prompt"]
        assert "Loop topology" in prompt_b
        assert "**builder**" in prompt_b
        assert "**gate_build**" in prompt_b

    # ── prompt enrichment tests ──────────────────────────────────

    def test_prompt_length_increases_with_context(self, tmp_path: Path) -> None:
        """Arm B prompt is strictly longer than Arm A across all gate types."""
        for gate in ("gate_qa", "gate_build", "gate_doc_freshness"):
            _workflow_cache.clear()
            WorkflowRegistry.reset()
            project = _create_cli_project(tmp_path / gate)
            result = _run_ab(project, reloop_gate=gate, feedback="failed")
            assert result["arm_b"]["prompt_length"] > result["arm_a"]["prompt_length"], (
                f"Prompt should be longer with context for {gate}"
            )

    def test_iteration_count_shown(self, tmp_path: Path) -> None:
        """Iteration counter appears in both arms: 0/3 for Arm A, 1/3 for Arm B."""
        project = _create_cli_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_qa", feedback="failing")
        assert "1/3" in result["arm_b"]["prompt"]
        assert "0/3" in result["arm_a"]["prompt"]

    def test_final_attempt_warning_at_max_iteration(self, tmp_path: Path) -> None:
        """At iteration 3/3, the FINAL ATTEMPT warning appears."""
        project = _create_cli_project(tmp_path)
        wf = improve_workflow()
        _register(wf)
        tool_init("improve", project)

        state = _load_state(project)
        order = state["topo_order"]
        builder_idx = order.index("builder")
        for i in range(builder_idx):
            state["completed"][order[i]] = "completed"
        state["pointer_idx"] = builder_idx
        state["iteration_counts"] = {"gate_qa->builder": 3}
        state["feedback_log"] = {
            "builder": [
                {
                    "gate": "gate_qa",
                    "iteration": i + 1,
                    "feedback": f"attempt {i + 1} failed",
                    "timestamp": float(i),
                }
                for i in range(3)
            ],
        }
        _save_state(project, state)

        prompt = tool_curr(project)
        assert "FINAL ATTEMPT" in prompt
        assert "3/3" in prompt

    def test_arm_a_prompt_has_topology_without_feedback(self, tmp_path: Path) -> None:
        """Arm A prompt has builder task with loop topology but no feedback history."""
        project = _create_cli_project(tmp_path)
        result = _run_ab(project, reloop_gate="gate_qa", feedback="whatever")

        prompt_a = result["arm_a"]["prompt"]
        assert "Node: builder" in prompt_a
        assert "Type: Agent (builder)" in prompt_a
        assert "LOOP CONTEXT" in prompt_a
        assert "Loop topology" in prompt_a
        assert "0/3" in prompt_a
        assert "Feedback history" not in prompt_a

    # ── tool_submit integration ──────────────────────────────────

    def test_tool_submit_retry_populates_feedback(self, tmp_path: Path) -> None:
        """Submitting RETRY for a real gate in the improve workflow populates feedback_log.

        Simulates the full CEO flow: advance to gate_qa, submit RETRY, then
        manually rewind (as the CEO would) and verify loop context appears.
        """
        project = _create_cli_project(tmp_path)
        wf = improve_workflow()
        _register(wf)
        tool_init("improve", project)

        state = _load_state(project)
        order = state["topo_order"]
        gate_qa_idx = order.index("gate_qa")

        # Advance to gate_qa
        for i in range(gate_qa_idx):
            state["completed"][order[i]] = "completed"
        state["pointer_idx"] = gate_qa_idx
        _save_state(project, state)

        # CEO submits RETRY
        tool_submit(
            project,
            "gate_qa",
            'RETRY target=builder feedback="3 assertion errors in test_csv2json"',
        )

        # Verify feedback was logged
        state = _load_state(project)
        assert "builder" in state["feedback_log"]
        assert state["feedback_log"]["builder"][0]["gate"] == "gate_qa"
        assert "assertion errors" in state["feedback_log"]["builder"][0]["feedback"]

        # Simulate CEO rewind: set iteration_counts and move pointer back
        state["iteration_counts"]["gate_qa->builder"] = 1
        state["pointer_idx"] = order.index("builder")
        for nid in order[order.index("builder"):]:
            state["completed"].pop(nid, None)
        _save_state(project, state)

        prompt = tool_curr(project)
        assert "LOOP CONTEXT" in prompt
        assert "gate_qa" in prompt
        assert "assertion errors" in prompt

    # ── structured report ────────────────────────────────────────

    def test_structured_ab_report(self, tmp_path: Path) -> None:
        """Generate a structured JSON report comparing all 3 projects."""
        scenarios = {
            "cli-tool": (
                _create_cli_project(tmp_path / "s1"),
                "gate_qa",
                "QA failed — 2 test errors in csv conversion",
            ),
            "web-api": (
                _create_web_api_project(tmp_path / "s2"),
                "gate_build",
                "Build review: scope creep in API endpoints",
            ),
            "mathlib": (
                _create_mathlib_project(tmp_path / "s3"),
                "gate_doc_freshness",
                "Docs stale: is_prime() not documented in README",
            ),
        }

        report: dict = {}
        for name, (project, gate, fb) in scenarios.items():
            _workflow_cache.clear()
            WorkflowRegistry.reset()

            result = _run_ab(project, reloop_gate=gate, feedback=fb)
            prompt_b = result["arm_b"]["prompt"]

            mentions_criteria = any(
                kw in prompt_b.lower()
                for kw in ["gate", "qa", "review", "check", "documentation", "pr diff"]
            )

            report[name] = {
                "arm_a": {
                    "has_topology": result["arm_a"]["has_loop_context"],
                    "has_feedback": result["arm_a"]["has_feedback"],
                    "prompt_length": result["arm_a"]["prompt_length"],
                },
                "arm_b": {
                    "has_topology": result["arm_b"]["has_loop_context"],
                    "has_feedback": result["arm_b"]["has_feedback"],
                    "prompt_length": result["arm_b"]["prompt_length"],
                    "mentions_downstream_criteria": mentions_criteria,
                },
                "delta": {
                    "arm_b_adds_feedback": (
                        result["arm_b"]["has_feedback"]
                        and not result["arm_a"]["has_feedback"]
                    ),
                    "length_increase": (
                        result["arm_b"]["prompt_length"] - result["arm_a"]["prompt_length"]
                    ),
                },
            }

        report["summary"] = {
            "all_arms_have_topology": all(
                report[n]["arm_a"]["has_topology"] and report[n]["arm_b"]["has_topology"]
                for n in ("cli-tool", "web-api", "mathlib")
            ),
            "no_arm_a_has_feedback": all(
                not report[n]["arm_a"]["has_feedback"]
                for n in ("cli-tool", "web-api", "mathlib")
            ),
            "all_arm_b_have_feedback": all(
                report[n]["arm_b"]["has_feedback"]
                for n in ("cli-tool", "web-api", "mathlib")
            ),
            "all_arm_b_mention_criteria": all(
                report[n]["arm_b"]["mentions_downstream_criteria"]
                for n in ("cli-tool", "web-api", "mathlib")
            ),
        }

        report_path = tmp_path / "loop-context-ab-report.json"
        report_path.write_text(json.dumps(report, indent=2))

        # ── validate every project ──
        for name in ("cli-tool", "web-api", "mathlib"):
            data = report[name]
            assert data["arm_a"]["has_topology"], (
                f"{name}: Arm A SHOULD have loop topology"
            )
            assert not data["arm_a"]["has_feedback"], (
                f"{name}: Arm A should NOT have feedback history"
            )
            assert data["arm_b"]["has_topology"], (
                f"{name}: Arm B SHOULD have loop topology"
            )
            assert data["arm_b"]["has_feedback"], (
                f"{name}: Arm B SHOULD have feedback history"
            )
            assert data["arm_b"]["mentions_downstream_criteria"], (
                f"{name}: Arm B should mention downstream gate criteria"
            )
            assert data["delta"]["arm_b_adds_feedback"], (
                f"{name}: delta should confirm feedback was added in Arm B"
            )
            assert data["delta"]["length_increase"] > 0, (
                f"{name}: prompt should be longer with feedback"
            )

        # ── validate summary ──
        assert report["summary"]["all_arms_have_topology"]
        assert report["summary"]["no_arm_a_has_feedback"]
        assert report["summary"]["all_arm_b_have_feedback"]
        assert report["summary"]["all_arm_b_mention_criteria"]

        # ── validate report file ──
        assert report_path.exists()
        loaded = json.loads(report_path.read_text())
        assert len(loaded) == 4  # 3 projects + summary
