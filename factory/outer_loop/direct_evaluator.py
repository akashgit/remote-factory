"""Direct FeatureBench evaluator — runs agents on the host, verifies in Docker.

Three-step architecture:
1. Extract /testbed/ from Docker image to a local temp dir
2. Run factory agents DIRECTLY ON THE HOST against the extracted testbed
3. Copy modified testbed back into a Docker container and run test.sh

This avoids installing agents inside Docker containers entirely.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog

from factory.outer_loop.models import EvalResult
from factory.workflow.primitives import AgentNode, ForkNode, GateNode, JoinNode, Workflow

log = structlog.get_logger()

_FEATUREBENCH_DIR = Path(__file__).resolve().parents[2] / "featurebench"
_REWARD_RE = re.compile(r"Reward:\s*(\d+)")


def _parse_from_line(dockerfile: Path) -> str:
    """Extract the base image from a Dockerfile's FROM line."""
    for line in dockerfile.read_text().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            return stripped.split()[1]
    raise ValueError(f"No FROM line found in {dockerfile}")


def _parse_deleted_files(patch_path: Path) -> list[str]:
    """Parse file paths deleted by a diff (--- a/path lines in deleted-file hunks)."""
    deleted: list[str] = []
    if not patch_path.exists():
        return deleted
    text = patch_path.read_text()
    in_delete_block = False
    for line in text.splitlines():
        if line.startswith("deleted file"):
            in_delete_block = True
        elif line.startswith("diff --git"):
            in_delete_block = False
        elif in_delete_block and line.startswith("--- a/"):
            deleted.append(line[6:])
    return deleted


def _topo_sort_nodes(workflow: Workflow) -> list[str]:
    """Topological sort of workflow nodes using Kahn's algorithm."""
    adj: dict[str, list[str]] = {nid: [] for nid in workflow.nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in workflow.nodes}
    for edge in workflow.edges:
        if edge.source in adj and edge.target in in_degree:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order: list[str] = []
    while queue:
        queue.sort()
        node = queue.pop(0)
        order.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return order


class DirectFeatureBenchEvaluator:
    """Evaluates workflows on FeatureBench without installing agents in containers.

    Implements the ``EvaluatorFn`` protocol::

        __call__(workflow, project_dir, instances) -> EvalResult
    """

    def __init__(
        self,
        featurebench_dir: Path | None = None,
        agent_timeout: int = 1800,
    ) -> None:
        self._featurebench_dir = featurebench_dir or _FEATUREBENCH_DIR
        self._agent_timeout = agent_timeout

    def __call__(
        self,
        workflow: Workflow,
        project_dir: str,
        instances: list[str],
    ) -> EvalResult:
        resolved = 0
        total = len(instances)
        per_instance: dict[str, object] = {}

        for instance_id in instances:
            success = self._eval_instance(workflow, instance_id)
            per_instance[instance_id] = {"resolved": success}
            if success:
                resolved += 1

        score = resolved / max(total, 1)
        log.info(
            "direct_eval_done",
            resolved=resolved,
            total=total,
            score=score,
        )
        return EvalResult(
            score=score,
            benchmark_score=score,
            complexity=float(len(workflow.nodes)),
            details={"instances": per_instance},
        )

    def _eval_instance(self, workflow: Workflow, instance_id: str) -> bool:
        """Evaluate a single FeatureBench instance. Returns True if resolved."""
        task_dir = self._featurebench_dir / instance_id
        if not task_dir.exists():
            log.error("task_dir_missing", instance=instance_id)
            return False

        dockerfile = task_dir / "environment" / "Dockerfile"
        if not dockerfile.exists():
            log.error("dockerfile_missing", instance=instance_id)
            return False

        image = _parse_from_line(dockerfile)
        workdir = Path(tempfile.mkdtemp(prefix=f"fb-{instance_id[:30]}-"))

        try:
            # 1. Pull image if needed
            log.info("pulling_image", image=image, instance=instance_id)
            subprocess.run(
                ["docker", "pull", "--platform", "linux/amd64", image],
                capture_output=True,
                text=True,
                timeout=600,
            )

            # 2. Extract /testbed/ from Docker image
            log.info("extracting_testbed", instance=instance_id)
            cid_result = subprocess.run(
                ["docker", "create", "--platform", "linux/amd64", image],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if cid_result.returncode != 0:
                log.error("docker_create_failed", stderr=cid_result.stderr, instance=instance_id)
                return False

            cid = cid_result.stdout.strip()
            try:
                cp_result = subprocess.run(
                    ["docker", "cp", f"{cid}:/testbed", str(workdir / "testbed")],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if cp_result.returncode != 0:
                    log.error("docker_cp_failed", stderr=cp_result.stderr, instance=instance_id)
                    return False
            finally:
                subprocess.run(["docker", "rm", cid], capture_output=True, timeout=30)

            testbed = workdir / "testbed"

            # 3. Initialize git in testbed if not already a repo
            if not (testbed / ".git").exists():
                subprocess.run(["git", "init"], cwd=testbed, capture_output=True, timeout=30)
                subprocess.run(["git", "add", "."], cwd=testbed, capture_output=True, timeout=60)
                subprocess.run(
                    ["git", "commit", "-m", "initial"],
                    cwd=testbed,
                    capture_output=True,
                    timeout=60,
                    env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test",
                         "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test",
                         "PATH": "/usr/bin:/bin:/usr/local/bin"},
                )

            # 4. Apply setup_patch (scramble the implementation)
            setup_patch = task_dir / "environment" / "setup_patch.diff"
            if setup_patch.exists() and setup_patch.stat().st_size > 0:
                log.info("applying_setup_patch", instance=instance_id)
                subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", str(setup_patch)],
                    cwd=testbed,
                    capture_output=True,
                    timeout=30,
                )

                # Delete test files listed in test_patch.diff (lv1)
                test_patch = task_dir / "environment" / "test_patch.diff"
                deleted_files = _parse_deleted_files(test_patch)
                for f in deleted_files:
                    target = testbed / f
                    if target.exists():
                        target.unlink()
                        log.debug("deleted_test_file", file=f, instance=instance_id)

            # 5. Copy instruction.md to testbed
            instruction = task_dir / "instruction.md"
            if instruction.exists():
                shutil.copy(instruction, testbed / "task-instruction.md")

            # 6. Create .factory dir for agent output
            factory_dir = testbed / ".factory"
            factory_dir.mkdir(exist_ok=True)
            (factory_dir / "reviews").mkdir(exist_ok=True)

            # 7. Run the workflow's agents on the testbed
            log.info("running_agents", instance=instance_id, nodes=len(workflow.nodes))
            self._run_workflow_agents(workflow, testbed)

            # 8. Verify: run test.sh inside Docker with the modified testbed mounted
            log.info("verifying_in_docker", instance=instance_id)
            success = self._verify_in_docker(task_dir, image, testbed)
            log.info(
                "instance_result",
                instance=instance_id,
                resolved=success,
            )
            return success

        except subprocess.TimeoutExpired:
            log.warning("instance_timeout", instance=instance_id)
            return False
        except Exception as exc:
            log.error("instance_error", instance=instance_id, error=str(exc))
            return False
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _run_workflow_agents(self, workflow: Workflow, testbed: Path) -> None:
        """Run workflow agents in topological order on the testbed."""
        order = _topo_sort_nodes(workflow)
        for node_id in order:
            node = workflow.nodes[node_id]
            if isinstance(node, AgentNode):
                timeout = node.timeout or self._agent_timeout
                prompt = node.prompt_template
                if not prompt:
                    continue

                log.info("running_agent", node=node_id, role=node.role.value, timeout=timeout)
                result = subprocess.run(
                    [
                        "factory",
                        "agent",
                        node.role.value,
                        "--task",
                        prompt,
                        "--project",
                        str(testbed),
                        "--timeout",
                        str(timeout),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout + 120,
                )
                log.info(
                    "agent_finished",
                    node=node_id,
                    returncode=result.returncode,
                    stdout_len=len(result.stdout),
                )
            elif isinstance(node, (GateNode, ForkNode, JoinNode)):
                pass

    def _verify_in_docker(
        self, task_dir: Path, image: str, testbed: Path
    ) -> bool:
        """Run test.sh inside Docker with the modified testbed mounted."""
        test_sh = task_dir / "tests" / "test.sh"
        if not test_sh.exists():
            log.error("test_sh_missing", task_dir=str(task_dir))
            return False

        # Copy test.sh and patches into testbed for the container
        shutil.copy(test_sh, testbed / ".test.sh")

        # Copy patches so test.sh can find them at /tmp/
        setup_patch = task_dir / "environment" / "setup_patch.diff"
        test_patch = task_dir / "environment" / "test_patch.diff"

        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "-v",
                f"{testbed}:/testbed",
                "-v",
                f"{test_sh}:/tmp/test.sh:ro",
                "-v",
                f"{setup_patch}:/tmp/setup_patch.diff:ro",
                "-v",
                f"{test_patch}:/tmp/test_patch.diff:ro",
                image,
                "bash",
                "/tmp/test.sh",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )

        log.info(
            "docker_verify_done",
            returncode=result.returncode,
            stdout_tail=result.stdout[-500:] if result.stdout else "",
            stderr_tail=result.stderr[-500:] if result.stderr else "",
        )

        # Parse reward from test.sh output
        reward_match = _REWARD_RE.search(result.stdout)
        if reward_match:
            reward = int(reward_match.group(1))
            return reward == 1

        return result.returncode == 0
