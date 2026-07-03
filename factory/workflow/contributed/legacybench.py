"""Legacy-Bench benchmark workflow — evaluates factory against Legacy-Bench.

Composes from improve_workflow() with:
- Researcher: output format analysis guidance
- Builder: legacy code preservation + hidden test awareness
- gate_build: legacy preservation enforcement (RELOOP if modernized)
- gate_qa: output format + decimal arithmetic enforcement
- auto_merge FnNode for containerized benchmark evaluation
"""

from typing import Any

from factory.models import ProjectState
from factory.workflow.definitions import improve_workflow
from factory.workflow.primitives import AgentNode, Edge, FnNode, GateNode

meta = {
    "name": "legacybench",
    "description": (
        "Legacy-Bench benchmark evaluation mode — full improve pipeline with "
        "auto-merge for containerized benchmarks. Targets legacy code: "
        "COBOL, Fortran, C, Java 7, Assembly."
    ),
}


def workflow():
    """Build the legacybench workflow by composing from improve."""
    wf = improve_workflow()

    # ── Researcher: add output format analysis ──────────────────
    researcher = wf.nodes["researcher"]
    assert isinstance(researcher, AgentNode)
    wf.nodes["researcher"] = researcher.model_copy(update={
        "prompt_template": (
            researcher.prompt_template + "\n\n"
            "For legacy codebases: trace multi-file data flows, identify business "
            "logic patterns, parse binary file formats, map dependencies. "
            "Document the EXACT output format the program produces: field widths, "
            "decimal places, alignment, separators, headers/footers. "
            "Write output format spec to .factory/strategy/output-format-spec.md"
        ),
        "writes": researcher.writes | {".factory/strategy/output-format-spec.md"},
    })

    # ── Builder: legacy guidance in prompt, no PR ───────────────
    builder = wf.nodes["builder"]
    assert isinstance(builder, AgentNode)
    wf.nodes["builder"] = builder.model_copy(update={
        "prompt_template": (
            "Implement the current hypothesis from .factory/strategy/current.md. "
            "Read CLAUDE.md and factory.md. Read the CEO strategy approval. "
            "Implement exactly what the hypothesis describes. Run tests. "
            "Commit locally — do NOT create a PR (benchmark mode).\n\n"
            "LEGACY CODE: Preserve the EXACT original language standard and "
            "coding patterns. Do NOT modernize syntax, idioms, or libraries. "
            "Fix ONLY the specific bug described in the hypothesis. "
            "If the bug requires changing a data type, use the equivalent "
            "type from the ORIGINAL language standard.\n\n"
            "HIDDEN TESTS: The benchmark uses hidden test inputs beyond the "
            "visible examples. Do NOT hardcode output to match reference "
            "examples. Implement the general algorithm that solves the problem "
            "for ANY valid input. Verify your fix works on at least 3 different "
            "inputs (visible + 2 you construct)."
        ),
        "reads": builder.reads | {".factory/strategy/output-format-spec.md"},
    })

    # ── gate_build: enforce legacy code preservation ────────────
    gate_build = wf.nodes["gate_build"]
    assert isinstance(gate_build, GateNode)
    wf.nodes["gate_build"] = gate_build.model_copy(update={
        "gate_prompt": (
            gate_build.gate_prompt + " "
            "LEGACY CHECK: Did the builder preserve the original language "
            "standard? Any modernized syntax, updated APIs, or changed "
            "idioms is a RELOOP. Did builder read the output format spec? "
            "REDIRECT if not."
        ),
    })

    # ── gate_qa: enforce output format + decimal verification ───
    gate_qa = wf.nodes["gate_qa"]
    assert isinstance(gate_qa, GateNode)
    wf.nodes["gate_qa"] = gate_qa.model_copy(update={
        "gate_prompt": (
            gate_qa.gate_prompt + " "
            "OUTPUT CHECK: Verify program output EXACTLY matches the format "
            "spec at .factory/strategy/output-format-spec.md (field widths, "
            "decimal places, separators). For decimal/currency calculations: "
            "independently verify with Python Decimal or bc — do NOT trust "
            "the program's self-reported output. RELOOP if any format "
            "mismatch or arithmetic discrepancy."
        ),
        "reads": (gate_qa.reads or set()) | {".factory/strategy/output-format-spec.md"},
    })

    # ── Auto-merge: new FnNode between finalize and archivist ───
    wf.nodes["auto_merge"] = FnNode(
        id="auto_merge",
        command=(
            "cd {project_path} && "
            "BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null "
            "| sed 's|refs/remotes/origin/||' || echo main) && "
            "CURRENT=$(git rev-parse --abbrev-ref HEAD) && "
            "git checkout \"$BASE\" && "
            "git merge --no-edit \"$CURRENT\" && "
            "git checkout \"$CURRENT\""
        ),
        reads={".factory/experiments/verdict.json"},
    )

    # ── Rewire edges: finalize → auto_merge → archivist ─────────
    wf.edges = [e for e in wf.edges if not (e.source == "finalize" and e.target == "archivist")]
    wf.edges.append(Edge(source="finalize", target="auto_merge"))
    wf.edges.append(Edge(source="auto_merge", target="archivist"))

    # ── Metadata ────────────────────────────────────────────────
    wf.name = "legacybench"

    def trigger(state: ProjectState, ctx: dict[str, Any]) -> bool:
        return ctx.get("mode") == "legacybench"

    wf.trigger = trigger
    return wf
