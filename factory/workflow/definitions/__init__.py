"""All workflow definitions as Python functions returning Workflow objects.

W1: Build Mode
W2: Design Mode (= W1 with user gate at strategy approval)
W3: Improve Mode
W4: Research Mode (= W3 with baseline+failure_analyst, deep-QA with surface checks, plateau gate)
W5: Meta Mode
W6: Discover Mode
W7: Review Mode
W8: Refine Mode
W9: Create Mode (meta-mode for creating new factory modes)
W10: Spec Generate Mode
W11: Spec Update Mode

All 5 core workflows (build, improve, research, refine, create) use the deep-QA
verification pipeline: 3 specialist agents (health_checker, code_reviewer,
adversarial_tester) with a single gate after code review to short-circuit on
critical bugs, replacing the monolithic QA agent.
"""

from factory.workflow.definitions._shared import DOC_FRESHNESS_GATE_PROMPT, _deep_qa_subgraph
from factory.workflow.definitions.build import build_workflow
from factory.workflow.definitions.create import create_workflow
from factory.workflow.definitions.design import design_workflow
from factory.workflow.definitions.discover import discover_workflow
from factory.workflow.definitions.doc import doc_generate_workflow, doc_update_workflow
from factory.workflow.definitions.founder import founder_workflow
from factory.workflow.definitions.improve import improve_workflow, qa_workflow
from factory.workflow.definitions.meta import meta_workflow
from factory.workflow.definitions.parallel_improve import parallel_improve_workflow
from factory.workflow.definitions.refine import refine_workflow
from factory.workflow.definitions.research import research_workflow
from factory.workflow.definitions.review import review_workflow
from factory.workflow.definitions.skill_refine import skill_refine_workflow
from factory.workflow.definitions.spec import spec_generate_workflow, spec_update_workflow
from factory.workflow.primitives import Workflow

__all__ = [
    "DOC_FRESHNESS_GATE_PROMPT",
    "_deep_qa_subgraph",
    "build_workflow",
    "design_workflow",
    "improve_workflow",
    "qa_workflow",
    "research_workflow",
    "meta_workflow",
    "discover_workflow",
    "review_workflow",
    "refine_workflow",
    "create_workflow",
    "skill_refine_workflow",
    "doc_generate_workflow",
    "doc_update_workflow",
    "spec_generate_workflow",
    "spec_update_workflow",
    "parallel_improve_workflow",
    "founder_workflow",
    "register_all",
]


def register_all() -> dict[str, Workflow]:
    """Build and return all workflow definitions."""
    from factory.workflow.contributed.featurebench import workflow as featurebench_workflow
    from factory.workflow.contributed.legacybench import workflow as legacybench_workflow
    from factory.workflow.contributed.programbench import workflow as programbench_workflow
    from factory.workflow.contributed.swebench import workflow as swebench_workflow
    from factory.workflow.contributed.terminalbench import workflow as terminalbench_workflow
    from factory.workflow.contributed.tomswe import workflow as tomswe_workflow
    from factory.workflow.deep_qa import workflow as deep_qa_workflow

    return {
        "build": build_workflow(),
        "design": design_workflow(),
        "discover": discover_workflow(),
        "review": review_workflow(),
        "improve": improve_workflow(),
        "parallel-improve": parallel_improve_workflow(),
        "qa": qa_workflow(),
        "deep-qa": deep_qa_workflow(),
        "legacybench": legacybench_workflow(),
        "featurebench": featurebench_workflow(),
        "programbench": programbench_workflow(),
        "swebench": swebench_workflow(),
        "terminalbench": terminalbench_workflow(),
        "tomswe": tomswe_workflow(),
        "research": research_workflow(),
        "meta": meta_workflow(),
        "refine": refine_workflow(),
        "create": create_workflow(),
        "skill-refine": skill_refine_workflow(),
        "doc-generate": doc_generate_workflow(),
        "doc-update": doc_update_workflow(),
        "spec-generate": spec_generate_workflow(),
        "spec-update": spec_update_workflow(),
        "founder": founder_workflow(),
    }
