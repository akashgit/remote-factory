"""Harbor evaluator — runs FeatureBench via Harbor to score workflow candidates.

Implements the ``EvaluatorFn`` protocol so ``SwarmEvaluator`` can use real
benchmark results as the fitness signal for evolutionary search.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path

import structlog
import yaml

from factory.outer_loop.models import EvalResult
from factory.workflow.primitives import (
    AgentNode,
    AgentRole,
    Edge,
    GateNode,
    Workflow,
)

log = structlog.get_logger()

_BENCHMARKS_DIR = Path(__file__).resolve().parents[2] / "benchmarks"
_RESOLVED_RE = re.compile(r"Result:\s*RESOLVED")
_COST_RE = re.compile(r'"cost_usd":\s*([0-9.]+)')


def create_seed_workflow() -> Workflow:
    """Build a simple 4-node seed workflow for FeatureBench evolution.

    Structure: researcher → builder → health_checker → gate

    The ``builder`` node ID matches the registered featurebench workflow
    so its prompt_template override takes effect during Harbor evaluation.
    """
    nodes: dict[str, AgentNode | GateNode] = {
        "researcher": AgentNode(
            id="researcher",
            role=AgentRole.RESEARCHER,
            prompt_template=(
                "Study the codebase and task. Read task-instruction.md in the project root. "
                "Explore the repository structure and identify files to modify. "
                "Write findings to .factory/reviews/study-output.md."
            ),
            writes={".factory/reviews/study-output.md"},
            timeout=300,
        ),
        "builder": AgentNode(
            id="builder",
            role=AgentRole.BUILDER,
            prompt_template=(
                "You are implementing a new feature in a Python codebase.\n\n"
                "1. Read the FULL task description at task-instruction.md in the project root.\n"
                "2. Read .factory/reviews/study-output.md for codebase context.\n"
                "3. CRITICAL: Read the actual source code for every function, class, "
                "or module you reference. Do NOT guess signatures or imports.\n"
                "4. Implement the feature following interface specs EXACTLY.\n"
                "5. Ensure all cross-file imports and references resolve correctly.\n"
                "6. Run the project's test suite.\n"
                "7. Fix any test failures — trace errors to root cause.\n"
                "8. Commit changes on the current branch.\n\n"
                "Rules:\n"
                "- Act AUTONOMOUSLY — do NOT ask for confirmation\n"
                "- Follow interface specs EXACTLY\n"
                "- Do NOT modify test files\n"
                "- Do NOT create branches or PRs — commit on current branch\n"
                "- Do NOT run factory commands"
            ),
            reads={".factory/reviews/study-output.md"},
            writes={".factory/reviews/builder-latest.md"},
            timeout=600,
        ),
        "health_checker": AgentNode(
            id="health_checker",
            role=AgentRole.HEALTH_CHECKER,
            prompt_template=(
                "Run the project's test suite and verify the implementation. "
                "Report test results and any issues found."
            ),
            reads={".factory/reviews/builder-latest.md"},
            writes={".factory/reviews/health-check.md"},
            timeout=600,
        ),
        "gate": GateNode(
            id="gate",
            evaluator_type="fn",
            evaluator_command=(
                'cd "$PROJECT_PATH" && '
                'CHANGES=$(git diff HEAD~1 --stat 2>/dev/null || echo NO_COMMITS) && '
                'if [ "$CHANGES" = "NO_COMMITS" ] || [ -z "$CHANGES" ]; then '
                'echo "HALT: no changes committed"; '
                'else echo "PROCEED"; fi'
            ),
            reads={".factory/reviews/health-check.md"},
        ),
    }
    edges = [
        Edge(source="researcher", target="builder"),
        Edge(source="builder", target="health_checker"),
        Edge(source="health_checker", target="gate"),
    ]
    return Workflow(
        name="featurebench-seed",
        nodes=nodes,  # type: ignore[arg-type]
        edges=edges,
        start_node="researcher",
    )


def workflow_to_harbor_yaml(wf: Workflow) -> str:
    """Convert a Workflow to a YAML annotation surface for Harbor override.

    Generates YAML that ``yaml_to_workflow()`` applies as prompt/timeout
    overrides on the registered featurebench workflow.  Only node IDs
    matching the registered workflow take effect; non-matching IDs are
    silently ignored.
    """
    surface: dict[str, dict[str, object]] = {}
    for node_id, node in wf.nodes.items():
        if isinstance(node, AgentNode) and node.prompt_template:
            slots: dict[str, object] = {
                f"task_prompt_{node_id}": node.prompt_template,
            }
            if node.timeout is not None:
                slots[f"timeout_{node_id}"] = node.timeout
            surface[node_id] = {"type": "AgentNode", "id": node_id, "slots": slots}
        elif isinstance(node, GateNode) and node.gate_prompt:
            surface[node_id] = {
                "type": "GateNode",
                "id": node_id,
                "slots": {f"gate_prompt_{node_id}": node.gate_prompt},
            }
    return yaml.dump(surface, default_flow_style=False)


class HarborEvaluator:
    """Evaluates workflow candidates by running FeatureBench instances via Harbor.

    Implements the ``EvaluatorFn`` protocol::

        __call__(workflow, project_dir, instances) -> EvalResult

    For each instance, runs ``benchmarks/run-harbor.sh featurebench --task <id>``
    with the workflow's prompt overrides injected via ``FACTORY_WORKFLOW_YAML_B64``.
    Parses stdout for resolved/not-resolved status and aggregates into a score.
    """

    def __init__(
        self,
        benchmarks_dir: Path | None = None,
        timeout: int = 300,
    ) -> None:
        self._benchmarks_dir = benchmarks_dir or _BENCHMARKS_DIR
        self._timeout = timeout
        self._script = self._benchmarks_dir / "run-harbor.sh"

    def __call__(
        self,
        workflow: Workflow,
        project_dir: str,
        instances: list[str],
    ) -> EvalResult:
        """Run the workflow on each instance via Harbor and return aggregate score."""
        if not self._script.exists():
            log.error("run_harbor_script_missing", path=str(self._script))
            return EvalResult(score=0.0, details={"error": "run-harbor.sh not found"})

        yaml_b64 = base64.b64encode(
            workflow_to_harbor_yaml(workflow).encode()
        ).decode()

        resolved = 0
        total = len(instances)
        total_cost = 0.0
        per_instance: dict[str, object] = {}

        for instance_id in instances:
            success, cost = self._run_instance(instance_id, yaml_b64)
            per_instance[instance_id] = {"resolved": success, "cost_usd": cost}
            if success:
                resolved += 1
            total_cost += cost

        score = resolved / max(total, 1)
        log.info(
            "harbor_eval_done",
            resolved=resolved,
            total=total,
            score=score,
            cost_usd=total_cost,
        )
        return EvalResult(
            score=score,
            benchmark_score=score,
            cost_usd=total_cost,
            complexity=float(len(workflow.nodes)),
            details={"instances": per_instance},
        )

    def _run_instance(
        self, instance_id: str, yaml_b64: str
    ) -> tuple[bool, float]:
        """Run a single instance via run-harbor.sh. Returns (resolved, cost_usd)."""
        cmd = [
            str(self._script),
            "featurebench",
            "--task",
            instance_id,
            "--timeout",
            str(self._timeout),
            "--preserve",
        ]
        env = dict(os.environ)
        env["FACTORY_WORKFLOW_YAML_B64"] = yaml_b64

        log.info("harbor_instance_start", instance=instance_id)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout * 3 + 300,
                env=env,
            )
        except subprocess.TimeoutExpired:
            log.warning("harbor_instance_timeout", instance=instance_id)
            return False, 0.0
        except (FileNotFoundError, OSError) as exc:
            log.error("harbor_instance_error", instance=instance_id, error=str(exc))
            return False, 0.0

        resolved = bool(_RESOLVED_RE.search(proc.stdout))
        cost_match = _COST_RE.search(proc.stdout)
        cost = float(cost_match.group(1)) if cost_match else 0.0

        log.info(
            "harbor_instance_done",
            instance=instance_id,
            resolved=resolved,
            cost_usd=cost,
            returncode=proc.returncode,
        )
        return resolved, cost
