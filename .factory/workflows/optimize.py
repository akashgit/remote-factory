"""Optimize workflow — single-function MCMC sampler optimization with correctness preservation.

18-node pipeline:
  precondition_check → fork_research → [3 researchers] → join_research → gate_research →
  strategist → gate_strategy (USER) → builder → gate_build → fork_qa →
  [conformance, tests, benchmark] → join_qa → gate_qa (RELOOP → builder, max 3) →
  archivist (async)

Requires --focus naming a specific sampler function.
Checks Reference/Reference.jl for existence.
Optimizes in Optimized/Optimized.jl.
"""

from __future__ import annotations

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
    "name": "optimize",
    "description": (
        "Optimize a single MCMC sampler function — parallel research, "
        "user-approved strategy, builder with RELOOP, parallel QA "
        "(conformance replay + tests + benchmark), async archival. "
        "Requires --focus <function_name>."
    ),
}


def workflow() -> Workflow:
    nodes: dict[str, AgentNode | FnNode | GateNode | ForkNode | JoinNode] = {}

    # ── Node 1: precondition_check (FnNode) ─────────────────────
    nodes["precondition_check"] = FnNode(
        id="precondition_check",
        command=(
            "cd {project_path} && "
            "FOCUS=$(echo '{focus}' | sed 's/[^a-zA-Z0-9_!]//g') && "
            'if [ -z "$FOCUS" ]; then '
            "echo 'HALT: --focus is required but empty'; exit 1; fi && "
            "if [ ! -f Reference/Reference.jl ]; then "
            "echo 'HALT: Reference/Reference.jl not found'; exit 1; fi && "
            'if ! grep -q "function ${FOCUS}" Reference/Reference.jl; then '
            'echo "HALT: function ${FOCUS} not found in Reference/Reference.jl"; exit 1; fi && '
            'echo "PROCEED: function ${FOCUS} found in Reference/Reference.jl" && '
            'if grep -q "function ${FOCUS}" Optimized/Optimized.jl 2>/dev/null; then '
            "echo 'BASELINE: optimized (existing Optimized implementation)'; "
            "else "
            "echo 'BASELINE: reference (no existing Optimized implementation)'; fi"
        ),
        writes=set(),
        notes=(
            "Validates --focus target exists in Reference/Reference.jl. "
            "Exits non-zero (HALT) if missing. Reports baseline source."
        ),
    )

    # ── Node 2: fork_research (ForkNode) ────────────────────────
    nodes["fork_research"] = ForkNode(
        id="fork_research",
        targets=["researcher_semantics", "researcher_conventions", "researcher_julia_perf"],
    )

    # ── Node 3: researcher_semantics (AgentNode) ────────────────
    nodes["researcher_semantics"] = AgentNode(
        id="researcher_semantics",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze the Reference implementation of the function specified by --focus "
            "in Reference/Reference.jl.\n\n"
            "Deliverables:\n"
            "1. Algorithm semantics: What does this sampler do mathematically?\n"
            "2. Data flow: Inputs -> transformations -> outputs\n"
            "3. Correctness invariants: What properties MUST be preserved?\n"
            "4. Deterministic replay contract: What makes outputs reproducible?\n"
            "5. Boundary conditions: Edge cases and numerical stability concerns\n"
            "6. Lean IR analysis: If verified-samplers-lean/ exists, check for "
            "formal specifications of this function\n\n"
            "Read:\n"
            "- Reference/Reference.jl (find the focus function)\n"
            "- Any Lean IR files in verified-samplers-lean/ if available\n"
            "- Project CLAUDE.md for context\n\n"
            "Write findings to: .factory/strategy/research-semantics.md"
        ),
        writes={".factory/strategy/research-semantics.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-semantics.md",
                must_exist=True,
                min_size=200,
                must_contain=["invariant"],
            )
        ],
    )

    # ── Node 4: researcher_conventions (AgentNode) ──────────────
    nodes["researcher_conventions"] = AgentNode(
        id="researcher_conventions",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Analyze existing Optimized implementations and project optimization conventions.\n\n"
            "Deliverables:\n"
            "1. Current optimizations applied to OTHER functions in Optimized/Optimized.jl\n"
            "2. PreparedMetric usage patterns and struct conventions\n"
            "3. Threading patterns: where and how @threads is applied\n"
            "4. Memory patterns: in-place mutation (!-suffix), buffer pre-allocation\n"
            "5. Type stability: Generic T<:AbstractFloat usage throughout\n"
            "6. Annotation patterns: @inline, @simd, @inbounds usage\n"
            "7. Function signature conventions (must match Reference exactly)\n\n"
            "Read:\n"
            "- Optimized/Optimized.jl (all functions, not just the focus target)\n"
            "- Reference/Reference.jl (for signature comparison)\n"
            "- CLAUDE.md and any project documentation\n\n"
            "Write findings to: .factory/strategy/research-conventions.md"
        ),
        writes={".factory/strategy/research-conventions.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-conventions.md",
                must_exist=True,
                min_size=200,
            )
        ],
    )

    # ── Node 5: researcher_julia_perf (AgentNode) ───────────────
    nodes["researcher_julia_perf"] = AgentNode(
        id="researcher_julia_perf",
        role=AgentRole.RESEARCHER,
        prompt_template=(
            "Research Julia performance optimization techniques relevant to MCMC samplers.\n\n"
            "Focus areas:\n"
            "1. Allocation elimination: avoiding heap allocations in hot loops\n"
            "2. Type stability: techniques for maintaining concrete types\n"
            "3. SIMD vectorization: @simd, LoopVectorization.jl patterns\n"
            "4. Cache optimization: memory layout and access patterns\n"
            "5. @inline, @inbounds, @fastmath annotations — when safe to use\n"
            "6. StaticArrays.jl for small fixed-size arrays\n"
            "7. Pre-allocation patterns for work buffers\n\n"
            "Search for:\n"
            "- Julia performance tips from official documentation\n"
            "- MCMC-specific Julia optimization techniques\n"
            "- BenchmarkTools.jl best practices for measuring speedup\n\n"
            "Write findings to: .factory/strategy/research-julia-perf.md"
        ),
        reads=set(),
        writes={".factory/strategy/research-julia-perf.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/research-julia-perf.md",
                must_exist=True,
                min_size=200,
            )
        ],
    )

    # ── Node 6: join_research (JoinNode) ────────────────────────
    nodes["join_research"] = JoinNode(
        id="join_research",
        sources=["researcher_semantics", "researcher_conventions", "researcher_julia_perf"],
        reads={
            ".factory/strategy/research-semantics.md",
            ".factory/strategy/research-conventions.md",
            ".factory/strategy/research-julia-perf.md",
        },
    )

    # ── Node 7: gate_research (GateNode) ────────────────────────
    nodes["gate_research"] = GateNode(
        id="gate_research",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review all 3 research reports for the optimize workflow:\n"
            "1. research-semantics.md — Must identify correctness invariants "
            "and the deterministic replay contract\n"
            "2. research-conventions.md — Must document existing optimization "
            "patterns (PreparedMetric, @threads, etc.)\n"
            "3. research-julia-perf.md — Must include actionable Julia "
            "performance techniques\n\n"
            "PROCEED if all 3 are substantive and cover their scope.\n"
            "RELOOP if any report is missing or too shallow."
        ),
        reads={
            ".factory/strategy/research-semantics.md",
            ".factory/strategy/research-conventions.md",
            ".factory/strategy/research-julia-perf.md",
        },
    )

    # ── Node 8: strategist (AgentNode) ──────────────────────────
    nodes["strategist"] = AgentNode(
        id="strategist",
        role=AgentRole.STRATEGIST,
        prompt_template=(
            "Synthesize the 3 research reports into a prioritized optimization "
            "strategy for the focus function.\n\n"
            "Read:\n"
            "- .factory/strategy/research-semantics.md\n"
            "- .factory/strategy/research-conventions.md\n"
            "- .factory/strategy/research-julia-perf.md\n"
            "- Reference/Reference.jl (the focus function)\n"
            "- Optimized/Optimized.jl (if existing baseline)\n\n"
            "Deliverables (write to .factory/strategy/current.md):\n"
            "1. Bottleneck analysis: where is time likely spent?\n"
            "2. Optimization opportunities ranked by expected impact:\n"
            "   - Allocation elimination\n"
            "   - Type stability improvements\n"
            "   - SIMD vectorization\n"
            "   - Threading (if not already applied)\n"
            "   - Cache-friendly memory access\n"
            "   - PreparedMetric struct additions\n"
            "3. Correctness preservation plan: how to verify each optimization "
            "maintains deterministic replay\n"
            "4. Risk assessment: low-risk vs high-risk optimizations\n"
            "5. Implementation order: which optimizations to apply first\n\n"
            "SACRED CONSTRAINTS (never violate):\n"
            "- Reference/Reference.jl is NEVER modified\n"
            "- Function signature must match Reference exactly\n"
            "- Deterministic replay: identical outputs for same RNG state\n"
            "- Generic typing: preserve T<:AbstractFloat parameterization"
        ),
        reads={
            ".factory/strategy/research-semantics.md",
            ".factory/strategy/research-conventions.md",
            ".factory/strategy/research-julia-perf.md",
        },
        writes={".factory/strategy/current.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/strategy/current.md",
                must_exist=True,
                min_size=500,
                must_contain=["Optimization", "Correctness"],
            )
        ],
    )

    # ── Node 9: gate_strategy (GateNode — USER) ────────────────
    nodes["gate_strategy"] = GateNode(
        id="gate_strategy",
        evaluator_type="user",
        gate_prompt=(
            "Review the optimization strategy at .factory/strategy/current.md.\n"
            "The strategy should include:\n"
            "- Ranked optimization opportunities with expected impact\n"
            "- Correctness preservation plan\n"
            "- Risk assessment for each optimization\n\n"
            "Approve to proceed to implementation, or provide feedback for revision."
        ),
        reads={".factory/strategy/current.md"},
    )

    # ── Node 10: builder (AgentNode) ────────────────────────────
    nodes["builder"] = AgentNode(
        id="builder",
        role=AgentRole.BUILDER,
        max_iterations=3,
        prompt_template=(
            "Implement the approved optimization strategy for the focus function "
            "in Optimized/Optimized.jl.\n\n"
            "Read:\n"
            "- .factory/strategy/current.md (approved strategy)\n"
            "- Reference/Reference.jl (the canonical implementation — NEVER modify)\n"
            "- Optimized/Optimized.jl (current state — your target file)\n"
            "- .factory/strategy/research-semantics.md (correctness invariants)\n"
            "- .factory/strategy/research-conventions.md (project patterns)\n\n"
            "SACRED CONSTRAINTS:\n"
            "- NEVER modify Reference/Reference.jl\n"
            "- Function signature MUST match Reference exactly\n"
            "- Deterministic replay: identical outputs for same RNG state\n"
            "- Preserve Generic typing: T<:AbstractFloat\n"
            "- All existing tests must continue to pass\n\n"
            "Implementation:\n"
            "1. Apply optimizations from strategy in priority order\n"
            "2. Follow project conventions (PreparedMetric, @threads patterns)\n"
            "3. Ensure type stability (use @code_warntype if available)\n"
            "4. Run 'make test' to verify nothing is broken\n"
            "5. Commit changes with descriptive message\n\n"
            "If this is a RELOOP iteration, read .factory/reviews/qa-*.md for "
            "feedback on what failed and fix those specific issues.\n\n"
            "Write to: Optimized/Optimized.jl\n"
            "Commit changes on the current branch."
        ),
        reads={
            ".factory/strategy/current.md",
            ".factory/strategy/research-semantics.md",
            ".factory/strategy/research-conventions.md",
        },
        writes={".factory/reviews/builder-latest.md"},
        post_checks=[
            ArtifactCheck(
                path=".factory/reviews/builder-latest.md",
                must_exist=True,
                min_size=200,
                must_contain=["commit"],
            )
        ],
    )

    # ── Node 11: gate_build (GateNode) ──────────────────────────
    nodes["gate_build"] = GateNode(
        id="gate_build",
        evaluator_type="agent",
        evaluator_role=AgentRole.CEO,
        gate_prompt=(
            "Review builder output for the optimize workflow:\n"
            "1. Optimized/Optimized.jl was modified (check git diff)\n"
            "2. Builder committed changes (check builder-latest.md for commit hash)\n"
            "3. No obvious syntax errors or incomplete code\n"
            "4. Reference/Reference.jl was NOT modified (SACRED — verify)\n\n"
            "PROCEED to QA if build looks complete.\n"
            "RELOOP to builder if issues found (max 3 iterations)."
        ),
        reads={".factory/reviews/builder-latest.md"},
    )

    # ── Node 12: fork_qa (ForkNode) ─────────────────────────────
    nodes["fork_qa"] = ForkNode(
        id="fork_qa",
        targets=["qa_conformance", "qa_tests", "qa_benchmark"],
    )

    # ── Node 13: qa_conformance (FnNode) ────────────────────────
    nodes["qa_conformance"] = FnNode(
        id="qa_conformance",
        command=(
            "cd {project_path} && "
            "julia --project=. -e '"
            "using Evaluation; "
            "results = Evaluation.Conformance.run_conformance(); "
            "any_fail = any(r -> !r.passed, results); "
            "for r in results; "
            'println(r.passed ? "PASS" : "FAIL", ": ", r.name); '
            "end; "
            "exit(any_fail ? 1 : 0)"
            "' 2>&1 | tee .factory/reviews/qa-conformance.md"
        ),
        writes={".factory/reviews/qa-conformance.md"},
        notes=(
            "Runs Evaluation.Conformance to verify Optimized outputs match "
            "Reference exactly under deterministic replay. "
            "Exit 0 = all pass, exit 1 = any failure."
        ),
    )

    # ── Node 14: qa_tests (FnNode) ──────────────────────────────
    nodes["qa_tests"] = FnNode(
        id="qa_tests",
        command=(
            "cd {project_path} && "
            "make test 2>&1 | tee .factory/reviews/qa-tests.md; "
            "exit ${PIPESTATUS[0]}"
        ),
        writes={".factory/reviews/qa-tests.md"},
        notes="Run full test suite. Exit 0 = all pass, exit 1 = any failure.",
    )

    # ── Node 15: qa_benchmark (FnNode) ──────────────────────────
    nodes["qa_benchmark"] = FnNode(
        id="qa_benchmark",
        command=(
            "cd {project_path} && "
            "julia --project=. -e '"
            "using Evaluation; "
            'result = Evaluation.OptimizationTrial.run_trial("{focus}"); '
            'println("Baseline: ", result.baseline_time); '
            'println("Optimized: ", result.optimized_time); '
            'println("Speedup: ", result.speedup, "x"); '
            "threshold = {speedup_threshold}; "
            "if result.speedup >= threshold; "
            'println("PASS: speedup ", result.speedup, "x >= threshold ", threshold, "x"); '
            "exit(0); "
            "else; "
            'println("FAIL: speedup ", result.speedup, "x < threshold ", threshold, "x"); '
            "exit(1); "
            "end"
            "' 2>&1 | tee .factory/reviews/qa-benchmark.md"
        ),
        writes={".factory/reviews/qa-benchmark.md"},
        notes=(
            "Runs OptimizationTrial benchmark. Compares Optimized vs baseline. "
            "Exit 0 = speedup >= threshold, exit 1 = below threshold. "
            "Default speedup_threshold = 1.0 (no regression). "
            "CEO substitutes {focus} and {speedup_threshold} at runtime."
        ),
    )

    # ── Node 16: join_qa (JoinNode) ─────────────────────────────
    nodes["join_qa"] = JoinNode(
        id="join_qa",
        sources=["qa_conformance", "qa_tests", "qa_benchmark"],
        reads={
            ".factory/reviews/qa-conformance.md",
            ".factory/reviews/qa-tests.md",
            ".factory/reviews/qa-benchmark.md",
        },
    )

    # ── Node 17: gate_qa (GateNode — fn) ────────────────────────
    nodes["gate_qa"] = GateNode(
        id="gate_qa",
        evaluator_type="fn",
        evaluator_command=(
            "cd {project_path} && "
            "CONFORMANCE=$(grep -c 'FAIL' .factory/reviews/qa-conformance.md 2>/dev/null || echo '1') && "
            "TESTS=$(grep -cE 'FAIL|Error|error' .factory/reviews/qa-tests.md 2>/dev/null || echo '1') && "
            "BENCHMARK=$(grep -c 'FAIL' .factory/reviews/qa-benchmark.md 2>/dev/null || echo '1') && "
            'if [ "$CONFORMANCE" -gt 0 ] || [ "$TESTS" -gt 0 ] || [ "$BENCHMARK" -gt 0 ]; then '
            "echo 'RELOOP: QA failed —'; "
            '[ "$CONFORMANCE" -gt 0 ] && echo \'  - Conformance replay: FAILED (outputs differ from Reference)\'; '
            '[ "$TESTS" -gt 0 ] && echo \'  - Test suite: FAILED\'; '
            '[ "$BENCHMARK" -gt 0 ] && echo \'  - Benchmark: FAILED (below speedup threshold)\'; '
            "exit 1; "
            "else "
            "echo 'PROCEED: All QA checks passed'; "
            "exit 0; fi"
        ),
        reads={
            ".factory/reviews/qa-conformance.md",
            ".factory/reviews/qa-tests.md",
            ".factory/reviews/qa-benchmark.md",
        },
    )

    # ── Node 18: archivist (AgentNode — async) ──────────────────
    nodes["archivist"] = AgentNode(
        id="archivist",
        role=AgentRole.ARCHIVIST,
        blocking=False,
        prompt_template=(
            "Record optimization results for the focus function.\n\n"
            "Read:\n"
            "- .factory/strategy/current.md (optimization strategy)\n"
            "- .factory/reviews/builder-latest.md (implementation notes)\n"
            "- .factory/reviews/qa-conformance.md (conformance result)\n"
            "- .factory/reviews/qa-tests.md (test result)\n"
            "- .factory/reviews/qa-benchmark.md (benchmark result)\n\n"
            "Archive:\n"
            "1. Optimization strategy (what was attempted)\n"
            "2. Implementation approach (how it was done)\n"
            "3. Benchmark results (speedup achieved)\n"
            "4. Key learnings (what worked, what didn't)\n"
            "5. Correctness verification (how determinism was confirmed)\n\n"
            "Write to: .factory/archive/optimization-record.md"
        ),
        reads={
            ".factory/strategy/current.md",
            ".factory/reviews/builder-latest.md",
            ".factory/reviews/qa-conformance.md",
            ".factory/reviews/qa-tests.md",
            ".factory/reviews/qa-benchmark.md",
        },
        writes={".factory/archive/optimization-record.md"},
    )

    # ── Edges ───────────────────────────────────────────────────
    edges = [
        # Precondition → Research
        Edge(source="precondition_check", target="fork_research"),
        # Fork/Join research
        Edge(source="fork_research", target="researcher_semantics"),
        Edge(source="fork_research", target="researcher_conventions"),
        Edge(source="fork_research", target="researcher_julia_perf"),
        Edge(source="researcher_semantics", target="join_research"),
        Edge(source="researcher_conventions", target="join_research"),
        Edge(source="researcher_julia_perf", target="join_research"),
        # Research gate
        Edge(source="join_research", target="gate_research"),
        Edge(source="gate_research", target="strategist", condition=VerdictType.PROCEED),
        Edge(source="gate_research", target="fork_research", condition=VerdictType.RELOOP),
        # Strategy
        Edge(source="strategist", target="gate_strategy"),
        Edge(source="gate_strategy", target="builder", condition=VerdictType.PROCEED),
        # Builder → Build gate
        Edge(source="builder", target="gate_build"),
        Edge(source="gate_build", target="fork_qa", condition=VerdictType.PROCEED),
        Edge(source="gate_build", target="builder", condition=VerdictType.RELOOP),
        # Fork/Join QA
        Edge(source="fork_qa", target="qa_conformance"),
        Edge(source="fork_qa", target="qa_tests"),
        Edge(source="fork_qa", target="qa_benchmark"),
        Edge(source="qa_conformance", target="join_qa"),
        Edge(source="qa_tests", target="join_qa"),
        Edge(source="qa_benchmark", target="join_qa"),
        # QA gate with RELOOP to builder
        Edge(source="join_qa", target="gate_qa"),
        Edge(source="gate_qa", target="archivist", condition=VerdictType.PROCEED),
        Edge(source="gate_qa", target="builder", condition=VerdictType.RELOOP),
    ]

    # ── Trigger ─────────────────────────────────────────────────
    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "optimize" and bool(ctx.get("focus"))

    return Workflow(
        name="optimize",
        nodes=nodes,
        edges=edges,
        start_node="precondition_check",
        terminal=True,
        trigger=trigger,
    )
