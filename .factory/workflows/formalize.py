"""Formalize mode — MCMC algorithm idea -> Lean-verified implementation + Julia reference.

Pipeline:
  fork_research -> [3 researchers] -> join_research -> gate_research ->
  strategist -> gate_strategy -> archivist_plan(async) ->
  builder_theory(max 5) -> gate_theory -> gate_theory_review ->
  builder_ir(max 3) -> gate_ir ->
  fn_generate -> fork_qa -> [3 FnNodes] -> join_qa -> gate_qa ->
  archivist_build(async)

Reloop edges:
  gate_theory -> builder_theory (max 5)
  gate_ir -> builder_ir (max 3)
  gate_qa -> builder_ir (max 3)

Terminal mode. Focus-only (requires --focus).
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    ArtifactCheck,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    VerdictType,
    Workflow,
)

meta = {
    "name": "formalize",
    "description": (
        "Formalize mode — turn MCMC algorithm ideas into Lean-verified "
        "implementations with auto-generated Reference Julia code. "
        "Focus-only terminal workflow. Use with --focus describing the "
        "algorithm to formalize."
    ),
}


def workflow() -> Workflow:
    """Build the formalize workflow."""
    nodes: dict[str, AgentNode | FnNode | GateNode | ForkNode | JoinNode] = {}
    edges: list[Edge] = []

    # ── Research Phase — Fork/Join/Gate ──────────────────────────

    nodes["fork_research"] = ForkNode(
        id="fork_research",
        targets=["researcher_patterns", "researcher_mathlib", "researcher_algorithm"],
    )

    nodes["researcher_patterns"] = AgentNode(
        id="researcher_patterns",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Formalization patterns analysis. "
            "Analyze how existing samplers are formalized in {project_path}/formal/Mcmc/. "
            "Study the pattern: Kernel theory (formal/Mcmc/Kernel/ or Mcmc/Hamiltonian/) "
            "-> Executable refinement (formal/Mcmc/Executable/Continuous/) "
            "-> CompilerIR program -> IRFormat emission. "
            "Read 2-3 existing examples end-to-end (e.g. RWMH: "
            "Kernel/GaussianRandomWalk.lean + Executable/Continuous/RWMH.lean "
            "+ Executable/Continuous/CompilerIR.lean). "
            "Document the module structure, naming conventions, import patterns, "
            "proof strategies, and how theorems connect kernel specs to executable refinements. "
            "Write findings to .factory/strategy/research-patterns.md."
        ),
        writes={".factory/strategy/research-patterns.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-patterns.md",
                must_exist=True,
                min_size=50,
            )
        ],
    )

    nodes["researcher_mathlib"] = AgentNode(
        id="researcher_mathlib",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Mathlib API discovery for the target algorithm. "
            "Read the --focus description from the CEO task to understand "
            "which algorithm is being formalized. "
            "Search {project_path}/.lake/packages/mathlib/Mathlib/ for relevant lemmas "
            "covering: measure theory, probability, linear algebra, topology, analysis. "
            "Check {project_path}/formal/lean-toolchain for the pinned Lean/mathlib version. "
            "Document available theorems that the formalization can reuse — "
            "provide exact module paths and theorem names. "
            "Write findings to .factory/strategy/research-mathlib.md."
        ),
        writes={".factory/strategy/research-mathlib.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-mathlib.md",
                must_exist=True,
                min_size=50,
            )
        ],
    )

    nodes["researcher_algorithm"] = AgentNode(
        id="researcher_algorithm",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Algorithm specification parsing. "
            "Read the --focus description from the CEO task. "
            "Parse the algorithm description into a precise mathematical specification. "
            "Identify: the state space, the proposal mechanism, the acceptance criterion, "
            "what needs to be proved (detailed balance, stationarity, invariance, reversibility), "
            "what IR primitives are needed (sample_gaussian, compute_log_density, etc.), "
            "and what the signature of the resulting executable function should be. "
            "Write findings to .factory/strategy/research-algorithm.md."
        ),
        writes={".factory/strategy/research-algorithm.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-algorithm.md",
                must_exist=True,
                min_size=50,
            )
        ],
    )

    nodes["join_research"] = JoinNode(
        id="join_research",
        sources=["researcher_patterns", "researcher_mathlib", "researcher_algorithm"],
    )

    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the three research outputs for the formalization. "
            "Check: (1) Are existing formalization patterns well-documented with concrete examples? "
            "(2) Are relevant mathlib lemmas identified with exact paths? "
            "(3) Is the algorithm specification mathematically precise with clear proof targets? "
            "PROCEED if all three are adequate. RELOOP if any research is shallow or missing key details."
        ),
        reads={
            ".factory/strategy/research-patterns.md",
            ".factory/strategy/research-mathlib.md",
            ".factory/strategy/research-algorithm.md",
        },
    )

    # ── Strategy Phase ──────────────────────────────────────────

    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Synthesize a formalization plan from the three research outputs. "
            "Read .factory/strategy/research-patterns.md, research-mathlib.md, "
            "and research-algorithm.md. "
            "Produce a concrete implementation plan covering: "
            "1) Lean module structure — which files to create under formal/Mcmc/ "
            "2) Theorem statements — what to prove and in what order "
            "3) IR program design — what CompilerIR programs to add "
            "4) IRFormat wiring — how to connect to formal/Mcmc/Executable/IRFormat.lean "
            "5) Mathlib reuse map — which existing lemmas to reference "
            "6) Module dependency graph — build order for lake build "
            "7) formal/Mcmc.lean update plan — new import lines "
            "Write the plan to .factory/strategy/current.md."
        ),
        reads={
            ".factory/strategy/research-patterns.md",
            ".factory/strategy/research-mathlib.md",
            ".factory/strategy/research-algorithm.md",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=200,
            )
        ],
    )

    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        reads={".factory/strategy/current.md"},
    )

    # ── Archivist (plan archive, non-blocking) ──────────────────

    nodes["archivist_plan"] = AgentNode(
        id="archivist_plan",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive the approved formalization plan. "
            "Record the algorithm being formalized, the module structure, "
            "theorem proof order, and mathlib dependencies."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/archive/formalize-plan.md"},
        blocking=False,
    )

    # ── Build Phase 1 — Theory + Executable Refinement ──────────

    nodes["builder_theory"] = AgentNode(
        id="builder_theory",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Implement the Lean kernel theory and executable refinement. "
            "Read the approved formalization plan at .factory/strategy/current.md. "
            "Read CLAUDE.md for project conventions. "
            "Create the Lean modules specified in the plan: "
            "- Kernel theory module(s) under formal/Mcmc/Kernel/ or appropriate subdirectory "
            "- Executable refinement module(s) under formal/Mcmc/Executable/ "
            "- Refinement theorems connecting the IR program to the mathematical kernel "
            "- Module docstrings and public definition docstrings per CLAUDE.md conventions "
            "Constraints: No sorry, admit, or axiom. Reuse mathlib lemmas from the plan. "
            "After writing the Lean code, run 'cd formal && lake build' to check compilation. "
            "If compilation fails, fix the errors before reporting completion. "
            "Commit changes when compilation succeeds."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
        max_iterations=5,
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md",
                must_exist=True,
                min_size=100,
            )
        ],
    )

    nodes["gate_theory"] = GateNode(
        id="gate_theory",
        evaluator_type="fn",
        evaluator_command="cd {project_path}/formal && lake build",
        gate_prompt=(
            "Lean proof compilation gate. The lake build command is the proof verifier — "
            "if it exits 0, all theorems type-check and the math is correct. "
            "RELOOP to builder_theory on compilation failure (max 5 iterations)."
        ),
    )

    nodes["gate_theory_review"] = GateNode(
        id="gate_theory_review",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the compiled Lean proofs. "
            "Read the builder output at .factory/reviews/builder-latest.md. "
            "Check git diff for the new .lean files. Verify: "
            "(1) Module structure matches the approved plan "
            "(2) Theorem names and types are meaningful "
            "(3) No sorry, admit, or axiom in the code "
            "(4) Module docstrings are present "
            "PROCEED to IR phase if proofs are well-structured. "
            "HALT if fundamental issues require re-planning."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Build Phase 2 — IR Emission + Import Wiring ─────────────

    nodes["builder_ir"] = AgentNode(
        id="builder_ir",
        role=AgentRole.BUILDER,
        prompt_template=(
            "Wire IR emission and update imports. "
            "Read the approved plan at .factory/strategy/current.md. "
            "Read CLAUDE.md for project conventions. "
            "Tasks: "
            "- Add IR program to CompilerIR (extend the program type with the new sampler) "
            "- Wire into formal/Mcmc/Executable/IRFormat.lean "
            "- Update formal/Mcmc.lean with new module imports "
            "After writing the code, run 'cd formal && lake build' to verify "
            "IR matches theory via refinement theorem. "
            "If compilation fails, fix the errors before reporting completion. "
            "Commit changes when compilation succeeds."
        ),
        reads={".factory/strategy/current.md"},
        writes={".factory/reviews/builder-latest.md"},
        max_iterations=3,
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md",
                must_exist=True,
                min_size=100,
            )
        ],
    )

    nodes["gate_ir"] = GateNode(
        id="gate_ir",
        evaluator_type="fn",
        evaluator_command="cd {project_path}/formal && lake build",
        gate_prompt=(
            "IR compilation gate. Verifies the IR emission matches the kernel theory "
            "via the refinement theorem. RELOOP to builder_ir on failure (max 3 iterations)."
        ),
    )

    # ── Reference Generation ────────────────────────────────────

    nodes["fn_generate"] = FnNode(
        id="fn_generate",
        command="cd {project_path} && make generate",
        notes="Emit updated Samplers.ir from the Lean IR programs. Single-shot, no retry.",
        writes={"Samplers.ir"},
    )

    # ── QA Phase — Fork/Join/Gate ───────────────────────────────

    nodes["fork_qa"] = ForkNode(
        id="fork_qa",
        targets=["fn_check_generated", "fn_test", "fn_proof_hygiene"],
    )

    nodes["fn_check_generated"] = FnNode(
        id="fn_check_generated",
        command="cd {project_path} && make check-generated",
        notes="Verify committed IR matches Lean source. Fails if IR is stale.",
    )

    nodes["fn_test"] = FnNode(
        id="fn_test",
        command="cd {project_path} && make test",
        notes="Run full test suite — Lean compilation + Julia tests including new Reference function.",
    )

    nodes["fn_proof_hygiene"] = FnNode(
        id="fn_proof_hygiene",
        command=(
            "cd {project_path}/formal && "
            "FILES=$(git diff --name-only HEAD~2 -- Mcmc/ | grep '\\.lean$' || true) && "
            "if [ -z \"$FILES\" ]; then "
            "  echo 'PASS: no new .lean files to check'; exit 0; "
            "fi && "
            "if echo \"$FILES\" | xargs grep -n 'sorry\\|admit\\|axiom'; then "
            "  echo 'FAIL: found sorry/admit/axiom in new Lean files'; exit 1; "
            "else "
            "  echo 'PASS: no proof holes found'; exit 0; "
            "fi"
        ),
        notes=(
            "Proof hygiene check — grep for sorry, admit, or axiom only in NEW .lean files "
            "(from recent git diff), not the entire Mcmc/ tree. "
            "Exit 0 if no new files or no matches. Exit 1 if matches found."
        ),
    )

    nodes["join_qa"] = JoinNode(
        id="join_qa",
        sources=["fn_check_generated", "fn_test", "fn_proof_hygiene"],
    )

    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review the three parallel QA results. "
            "All three must pass for PROCEED: "
            "(1) make check-generated — committed IR matches Lean source "
            "(2) make test — full test suite passes "
            "(3) proof hygiene — no sorry/admit/axiom in .lean files "
            "If any check failed, RELOOP to builder_ir with specific guidance "
            "on what to fix (max 3 iterations). "
            "If all pass, PROCEED to archivist."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Archivist (build archive, non-blocking) ─────────────────

    nodes["archivist_build"] = AgentNode(
        id="archivist_build",
        role=AgentRole.ARCHIVIST,
        prompt_template=(
            "Archive the formalization build results. "
            "Record: what algorithm was formalized, theorems proved, "
            "IR programs added, new Reference Julia functions generated, "
            "and any lessons learned from proof compilation iterations."
        ),
        reads={".factory/reviews/builder-latest.md"},
        writes={".factory/archive/formalize-build.md"},
        blocking=False,
    )

    # ── Edges ───────────────────────────────────────────────────

    edges = [
        # Research fork -> researchers -> join
        Edge(source="fork_research", target="researcher_patterns"),
        Edge(source="fork_research", target="researcher_mathlib"),
        Edge(source="fork_research", target="researcher_algorithm"),
        Edge(source="researcher_patterns", target="join_research"),
        Edge(source="researcher_mathlib", target="join_research"),
        Edge(source="researcher_algorithm", target="join_research"),
        Edge(source="join_research", target="gate_research"),
        # Research gate
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="fork_research", condition=VerdictType.RELOOP),
        # Strategy
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="archivist_plan", condition=VerdictType.PROCEED),
        Edge(source="gate_strategy", target="strategist", condition=VerdictType.RELOOP),
        # Archivist -> builder
        Edge(source="archivist_plan", target="builder_theory"),
        # Build Phase 1: Theory
        Edge(source="builder_theory", target="gate_theory"),
        Edge(source="gate_theory", target="gate_theory_review", condition=VerdictType.PROCEED),
        Edge(source="gate_theory", target="builder_theory", condition=VerdictType.RELOOP),
        # Theory review
        Edge(source="gate_theory_review", target="builder_ir", condition=VerdictType.PROCEED),
        Edge(source="gate_theory_review", target="archivist_build", condition=VerdictType.HALT),
        # Build Phase 2: IR
        Edge(source="builder_ir", target="gate_ir"),
        Edge(source="gate_ir", target="fn_generate", condition=VerdictType.PROCEED),
        Edge(source="gate_ir", target="builder_ir", condition=VerdictType.RELOOP),
        # Generate -> QA
        Edge(source="fn_generate", target="fork_qa"),
        Edge(source="fork_qa", target="fn_check_generated"),
        Edge(source="fork_qa", target="fn_test"),
        Edge(source="fork_qa", target="fn_proof_hygiene"),
        Edge(source="fn_check_generated", target="join_qa"),
        Edge(source="fn_test", target="join_qa"),
        Edge(source="fn_proof_hygiene", target="join_qa"),
        Edge(source="join_qa", target="gate_qa"),
        # QA gate
        Edge(source="gate_qa", target="archivist_build", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder_ir", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ─────────────────────────────────────────────────

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "formalize"

    return Workflow(
        name="formalize",
        nodes=nodes,
        edges=edges,
        start_node="fork_research",
        terminal=True,
        trigger=trigger,
    )
