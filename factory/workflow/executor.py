"""Deterministic async graph walker implementing formal execution semantics."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.events import (
    GateVerdictEvent,
    NodeCompleted,
    NodeFailed,
    NodeStarted,
    WorkflowCompleted,
    WorkflowHalted,
    WorkflowStarted,
    emit_workflow_event,
)
from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    Edge,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    NodeType,
    SelectionNode,
    Study,
    SubgraphForkNode,
    Verdict,
    VerdictType,
    Workflow,
)

log = structlog.get_logger()

CEO_GATE_PROMPT = """\
You are reviewing the output of the {step_name} step in the {workflow_name} workflow.
The output is at: {output_file}
Previous context: {previous_context}

Read the output and decide:
- **Proceed**: the output is satisfactory, continue to the next step
- **Reloop(target, feedback)**: the output needs improvement. Reloop targets: {reloop_targets}. Specify which step to return to and what feedback to provide.
- **Halt(reason)**: something is fundamentally wrong, stop the workflow.

Respond with exactly one of:
PROCEED
RELOOP target="<node_id>" feedback="<your feedback>"
HALT reason="<your reason>"
"""


class ExecutionResult:
    """Result of a workflow execution."""

    def __init__(self) -> None:
        self.success: bool = False
        self.halted: bool = False
        self.halt_reason: str = ""
        self.nodes_executed: int = 0
        self.events: list[dict[str, Any]] = []
        self.completed_files: set[str] = set()
        self.node_outputs: dict[str, str] = {}
        self.duration_ms: float = 0.0


class WorkflowExecutor:
    """Deterministic async graph walker for workflow execution."""

    def __init__(
        self,
        workflow: Workflow,
        project_path: Path,
        agent_pool: dict[str, AgentConfig] | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.workflow = workflow
        self.project_path = project_path
        self.agent_pool = agent_pool or {}
        self.dry_run = dry_run
        self.run_id = uuid.uuid4().hex[:12]
        self.completed_files: set[str] = set()
        self.node_context: dict[str, str] = {}
        self.iteration_counts: dict[tuple[str, str], int] = {}
        self.background_tasks: list[asyncio.Task[Any]] = []
        self.result = ExecutionResult()
        self._edge_index: dict[str, list[Edge]] = {}
        for edge in workflow.edges:
            self._edge_index.setdefault(edge.source, []).append(edge)

    async def execute(self) -> ExecutionResult:
        """Run the workflow from start to completion."""
        start_time = time.monotonic()

        self._emit(
            "workflow.started",
            WorkflowStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                start_node=self.workflow.start_node,
            ),
        )

        try:
            await self._execute_from(self.workflow.start_node)
            self.result.success = not self.result.halted
        except Exception as exc:
            self.result.success = False
            self.result.halted = True
            self.result.halt_reason = str(exc)
            log.error("workflow.exception", error=str(exc), workflow=self.workflow.name)

        if self.background_tasks:
            done, pending = await asyncio.wait(
                self.background_tasks,
                timeout=30.0,
            )
            for task in pending:
                task.cancel()

        elapsed = (time.monotonic() - start_time) * 1000
        self.result.duration_ms = elapsed
        self.result.completed_files = set(self.completed_files)

        if self.result.halted:
            self._emit(
                "workflow.halted",
                WorkflowHalted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    reason=self.result.halt_reason,
                    halted_at_node="unknown",
                ),
            )
        else:
            self._emit(
                "workflow.completed",
                WorkflowCompleted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    nodes_executed=self.result.nodes_executed,
                    duration_ms=elapsed,
                ),
            )

        return self.result

    async def _execute_from(self, node_id: str) -> None:
        """Execute starting from the given node, following edges."""
        if self.result.halted:
            return

        node = self.workflow.nodes.get(node_id)
        if not node:
            self.result.halted = True
            self.result.halt_reason = f"node '{node_id}' not found"
            return

        await self._wait_for_reads(node)
        if self.result.halted:
            return

        if isinstance(node, SubgraphForkNode):
            await self._execute_subgraph_fork(node)
            return

        if isinstance(node, ForkNode):
            await self._execute_fork(node)
            return

        if isinstance(node, SelectionNode):
            await self._execute_selection(node)
            return

        if isinstance(node, JoinNode):
            self.result.nodes_executed += 1
            self.completed_files |= node.writes
            next_id = self._next_unconditional(node_id)
            if next_id:
                await self._execute_from(next_id)
            return

        if isinstance(node, GateNode):
            await self._execute_gate(node)
            return

        await self._execute_action_node(node)

    async def _execute_action_node(self, node: NodeType) -> None:
        """Execute an AgentNode, FnNode, or Study node."""
        node_id = node.id
        node_type = type(node).__name__

        if not node.blocking:
            task = asyncio.create_task(self._run_node_background(node))
            self.background_tasks.append(task)
            next_id = self._next_unconditional(node_id)
            if next_id:
                await self._execute_from(next_id)
            return

        self._emit(
            "node.started",
            NodeStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node_id,
                node_type=node_type,
            ),
        )

        start = time.monotonic()
        try:
            output = await self._run_node(node)
            elapsed = (time.monotonic() - start) * 1000

            self.result.node_outputs[node_id] = output
            self.completed_files |= node.writes
            self.result.nodes_executed += 1

            self._emit(
                "node.completed",
                NodeCompleted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type=node_type,
                    files_written=sorted(node.writes),
                    duration_ms=elapsed,
                ),
            )

        except Exception as exc:
            self._emit(
                "node.failed",
                NodeFailed(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type=node_type,
                    error=str(exc),
                ),
            )
            self.result.halted = True
            self.result.halt_reason = f"node '{node_id}' failed: {exc}"
            return

        next_id = self._next_unconditional(node_id)
        if next_id:
            await self._execute_from(next_id)

    async def _run_node_background(self, node: NodeType) -> None:
        """Run a non-blocking node as a background task."""
        node_id = node.id
        node_type = type(node).__name__
        self._emit(
            "node.started",
            NodeStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node_id,
                node_type=node_type,
            ),
        )
        start = time.monotonic()
        try:
            output = await self._run_node(node)
            elapsed = (time.monotonic() - start) * 1000
            self.result.node_outputs[node_id] = output
            self.completed_files |= node.writes
            self.result.nodes_executed += 1
            self._emit(
                "node.completed",
                NodeCompleted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type=node_type,
                    files_written=sorted(node.writes),
                    duration_ms=elapsed,
                ),
            )
        except Exception as exc:
            self._emit(
                "node.failed",
                NodeFailed(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type=node_type,
                    error=str(exc),
                ),
            )
            log.warning("background_node_failed", node=node_id, error=str(exc))

    async def _execute_gate(self, node: GateNode) -> None:
        """Execute a gate node, parse verdict, follow the matching edge."""
        node_id = node.id
        self._emit(
            "node.started",
            NodeStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node_id,
                node_type="GateNode",
            ),
        )

        try:
            verdict = await self._evaluate_gate(node)
        except Exception as exc:
            self._emit(
                "node.failed",
                NodeFailed(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node_id,
                    node_type="GateNode",
                    error=str(exc),
                ),
            )
            self.result.halted = True
            self.result.halt_reason = f"gate '{node_id}' failed: {exc}"
            return

        self.result.nodes_executed += 1

        self._emit(
            "gate.verdict",
            GateVerdictEvent(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node_id,
                verdict_type=verdict.type,
                target=verdict.target,
                feedback=verdict.feedback,
                reason=verdict.reason,
            ),
        )

        if verdict.type == VerdictType.HALT:
            self.result.halted = True
            self.result.halt_reason = verdict.reason or "gate halted"
            return

        if verdict.type == VerdictType.RELOOP:
            target = verdict.target
            if not target:
                self.result.halted = True
                self.result.halt_reason = "reloop verdict missing target"
                return

            key = (node_id, target)
            count = self.iteration_counts.get(key, 0) + 1
            self.iteration_counts[key] = count

            if count > verdict.max_iterations:
                self.result.halted = True
                self.result.halt_reason = (
                    f"max iterations ({verdict.max_iterations}) exhausted "
                    f"for gate '{node_id}' -> '{target}'"
                )
                return

            if verdict.feedback:
                existing = self.node_context.get(target, "")
                self.node_context[target] = (
                    f"{existing}\n\n[Feedback iteration {count}]: {verdict.feedback}"
                    if existing
                    else f"[Feedback iteration {count}]: {verdict.feedback}"
                )

            await self._execute_from(target)
            return

        target_id = self._next_conditional(node_id, VerdictType.PROCEED)
        if target_id is None:
            target_id = self._next_unconditional(node_id)

        if target_id:
            await self._execute_from(target_id)

    async def _execute_fork(self, node: ForkNode) -> None:
        """Execute all fork targets concurrently via asyncio.gather.

        Branches are run in isolation — they do NOT follow outgoing edges.
        After all branches complete, the fork's own unconditional edge is followed.
        """
        self.result.nodes_executed += 1

        async def run_branch(target_id: str) -> None:
            target = self.workflow.nodes.get(target_id)
            if not target:
                return
            node_type = type(target).__name__
            self._emit(
                "node.started",
                NodeStarted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=target_id,
                    node_type=node_type,
                ),
            )
            start = time.monotonic()
            try:
                output = await self._run_node(target)
                elapsed = (time.monotonic() - start) * 1000
                self.result.node_outputs[target_id] = output
                self.completed_files |= target.writes
                self.result.nodes_executed += 1
                self._emit(
                    "node.completed",
                    NodeCompleted(
                        workflow_name=self.workflow.name,
                        run_id=self.run_id,
                        node_id=target_id,
                        node_type=node_type,
                        files_written=sorted(target.writes),
                        duration_ms=elapsed,
                    ),
                )
            except Exception as exc:
                self._emit(
                    "node.failed",
                    NodeFailed(
                        workflow_name=self.workflow.name,
                        run_id=self.run_id,
                        node_id=target_id,
                        node_type=node_type,
                        error=str(exc),
                    ),
                )
                if not self.result.halted:
                    self.result.halt_reason = f"fork branch '{target_id}' failed: {exc}"
                self.result.halted = True

        await asyncio.gather(*(run_branch(t) for t in node.targets))

        if self.result.halted:
            return

        branch_set = set(node.targets)
        next_id: str | None = None
        for edge in self._edge_index.get(node.id, []):
            if edge.condition is None and edge.target not in branch_set:
                next_id = edge.target
                break
        if next_id is None and node.targets:
            next_id = self._next_unconditional(node.targets[0])
        if next_id:
            await self._execute_from(next_id)

    async def _execute_subgraph_fork(self, node: SubgraphForkNode) -> None:
        """Execute N copies of a subgraph in parallel, each in an isolated worktree.

        Each branch gets an independent WorkflowExecutor with its own state,
        running against a separate git worktree branching from the same commit.
        """
        import subprocess as sp

        from factory.worktree import create_experiment_worktree

        self.result.nodes_executed += 1

        self._emit(
            "node.started",
            NodeStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node.id,
                node_type="SubgraphForkNode",
            ),
        )

        start = time.monotonic()

        # Resolve base commit for all branches
        if self.dry_run:
            base_commit = "0" * 40
        else:
            result = sp.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                check=True,
            )
            base_commit = result.stdout.strip()

        # Parse hypotheses from strategist output to determine branch count
        strategy_file = self.project_path / ".factory" / "strategy" / "current.md"
        hypotheses = _parse_hypotheses(strategy_file) if strategy_file.exists() else []
        branch_count = min(len(hypotheses), node.parallelism) if hypotheses else node.parallelism

        if branch_count < 1:
            branch_count = 1

        # Collect subgraph node IDs by walking edges from entry to exit
        subgraph_ids = _collect_subgraph_nodes(
            self.workflow, node.subgraph_entry, node.subgraph_exit,
        )
        sub_workflow = self.workflow.subgraph(
            subgraph_ids, name=f"{self.workflow.name}__branch", start_node=node.subgraph_entry,
        )

        branch_results: list[dict[str, Any]] = []
        worktrees: list[tuple[Path, str, int]] = []

        async def run_branch(idx: int) -> dict[str, Any]:
            from factory.store import ExperimentStore

            hypothesis = hypotheses[idx] if idx < len(hypotheses) else f"Hypothesis {idx + 1}"

            if self.dry_run:
                wt_path = self.project_path / ".factory-worktrees" / f"exp-dry-{idx}"
                branch_name = f"factory/exp-dry-{idx}"
                exp_id = idx + 1
            else:
                store = ExperimentStore(self.project_path)
                exp_id = await store.begin(hypothesis)
                wt_path, branch_name = create_experiment_worktree(
                    self.project_path, exp_id, base_commit,
                )
                worktrees.append((wt_path, branch_name, exp_id))

            branch_executor = WorkflowExecutor(
                sub_workflow.model_copy(deep=True),
                wt_path if not self.dry_run else self.project_path,
                agent_pool=self.agent_pool,
                dry_run=self.dry_run,
            )
            branch_result = await branch_executor.execute()

            return {
                "exp_id": exp_id,
                "hypothesis": hypothesis,
                "worktree_path": str(wt_path),
                "branch": branch_name,
                "success": branch_result.success,
                "halted": branch_result.halted,
                "halt_reason": branch_result.halt_reason,
                "nodes_executed": branch_result.nodes_executed,
                "node_outputs": branch_result.node_outputs,
            }

        sem = asyncio.Semaphore(node.parallelism)

        async def throttled_branch(idx: int) -> dict[str, Any]:
            async with sem:
                return await run_branch(idx)

        tasks = [throttled_branch(i) for i in range(branch_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, BaseException):
                log.warning("subgraph_branch_failed", error=str(r))
                branch_results.append({
                    "success": False, "halted": True, "halt_reason": str(r),
                })
            else:
                branch_results.append(r)  # type: ignore[arg-type]

        elapsed = (time.monotonic() - start) * 1000
        self.result.node_outputs[node.id] = json.dumps(branch_results)
        self.completed_files |= node.writes

        self._emit(
            "node.completed",
            NodeCompleted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node.id,
                node_type="SubgraphForkNode",
                files_written=sorted(node.writes),
                duration_ms=elapsed,
            ),
        )

        next_id = self._next_unconditional(node.id)
        if next_id:
            await self._execute_from(next_id)

    async def _execute_selection(self, node: SelectionNode) -> None:
        """Compare parallel experiment results and select the best."""
        import subprocess as sp

        from factory.worktree import remove_worktree

        self.result.nodes_executed += 1

        self._emit(
            "node.started",
            NodeStarted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node.id,
                node_type="SelectionNode",
            ),
        )

        start = time.monotonic()

        # Find the SubgraphForkNode's output (branch results)
        fork_output = ""
        for nid, output in self.result.node_outputs.items():
            try:
                parsed = json.loads(output)
                if isinstance(parsed, list) and parsed and "exp_id" in parsed[0]:
                    fork_output = output
                    break
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        if self.dry_run or not fork_output:
            selection_result: dict[str, Any] = {"strategy": node.strategy, "winner": None, "reason": "dry-run"}
            self.result.node_outputs[node.id] = json.dumps(selection_result)
            self.completed_files |= node.writes
            elapsed = (time.monotonic() - start) * 1000
            self._emit(
                "node.completed",
                NodeCompleted(
                    workflow_name=self.workflow.name,
                    run_id=self.run_id,
                    node_id=node.id,
                    node_type="SelectionNode",
                    files_written=sorted(node.writes),
                    duration_ms=elapsed,
                ),
            )
            next_id = self._next_unconditional(node.id)
            if next_id:
                await self._execute_from(next_id)
            return

        branches: list[dict[str, Any]] = json.loads(fork_output)
        successful = [b for b in branches if b.get("success")]

        if not successful:
            self.result.halted = True
            self.result.halt_reason = "all parallel experiment branches failed"
            return

        # best_score: read eval results from each worktree
        best: dict[str, Any] | None = None
        best_score = -1.0

        for branch in successful:
            wt_path = Path(branch["worktree_path"])
            eval_file = wt_path / ".factory" / "last_eval.json"
            score = 0.0
            if eval_file.exists():
                try:
                    data = json.loads(eval_file.read_text())
                    score = float(data.get("total", data.get("score", 0.0)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass

            branch["score"] = score
            if score > best_score:
                best_score = score
                best = branch

        if not best:
            best = successful[0]

        # Merge winner branch into baseline
        winner_branch = best["branch"]
        try:
            sp.run(
                ["git", "merge", winner_branch, "--no-edit", "-m",
                 f"Merge parallel experiment winner (exp {best['exp_id']})"],
                cwd=self.project_path,
                check=True,
                capture_output=True,
            )
        except sp.CalledProcessError as exc:
            log.error("selection_merge_failed", branch=winner_branch, error=str(exc))
            self.result.halted = True
            self.result.halt_reason = f"failed to merge winner branch {winner_branch}"
            return

        # Finalize losers as superseded, clean up all worktrees
        from factory.store import ExperimentStore

        store = ExperimentStore(self.project_path)
        for branch in branches:
            wt_path = Path(branch.get("worktree_path", ""))
            branch_name = branch.get("branch", "")
            exp_id = branch.get("exp_id")

            if branch is not best and exp_id is not None:
                from factory.models import ExperimentRecord
                record = ExperimentRecord(
                    id=exp_id,
                    timestamp=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
                    hypothesis=branch.get("hypothesis", ""),
                    change_summary="superseded by experiment " + str(best["exp_id"]),
                    issue_number=None,
                    pr_number=None,
                    score_before=None,
                    score_after=branch.get("score"),
                    delta=None,
                    verdict="superseded",
                    cost_usd=None,
                    notes="",
                )
                try:
                    await store.finalize(exp_id, record)
                except Exception as exc:
                    log.warning("finalize_superseded_failed", exp_id=exp_id, error=str(exc))

            if wt_path.exists() and branch_name:
                try:
                    remove_worktree(self.project_path, wt_path, branch_name)
                except Exception as exc:
                    log.warning("worktree_cleanup_failed", path=str(wt_path), error=str(exc))

        selection_result = {
            "strategy": node.strategy,
            "winner_exp_id": best["exp_id"],
            "winner_score": best.get("score", 0.0),
            "winner_hypothesis": best.get("hypothesis", ""),
            "total_branches": len(branches),
            "successful_branches": len(successful),
        }
        self.result.node_outputs[node.id] = json.dumps(selection_result)
        self.completed_files |= node.writes

        elapsed = (time.monotonic() - start) * 1000
        self._emit(
            "node.completed",
            NodeCompleted(
                workflow_name=self.workflow.name,
                run_id=self.run_id,
                node_id=node.id,
                node_type="SelectionNode",
                files_written=sorted(node.writes),
                duration_ms=elapsed,
            ),
        )

        next_id = self._next_unconditional(node.id)
        if next_id:
            await self._execute_from(next_id)

    async def _run_node(self, node: NodeType) -> str:
        """Execute a single node and return its output."""
        if self.dry_run:
            return f"[dry-run] {node.id} executed"

        if isinstance(node, Study):
            return await self._run_study(node)

        if isinstance(node, FnNode):
            return await self._run_fn(node)

        if isinstance(node, AgentNode):
            return await self._run_agent(node)

        return f"[unknown node type] {type(node).__name__}"

    async def _run_study(self, node: Study) -> str:
        """Run factory study command."""
        cmd = f"factory study {shlex.quote(str(self.project_path))}"
        if node.focus:
            cmd += f' --focus "{node.focus}"'
        return await self._run_shell(cmd)

    async def _run_fn(self, node: FnNode) -> str:
        """Run a FnNode's shell command."""
        if not node.command:
            return ""
        cmd = node.command.replace("{project_path}", shlex.quote(str(self.project_path)))
        return await self._run_shell(cmd)

    async def _run_agent(self, node: AgentNode) -> str:
        """Invoke an agent via factory/agents/runner.py."""
        from factory.agents.runner import invoke_agent

        task = node.prompt_template
        context = self.node_context.get(node.id, "")
        if context:
            task = f"{task}\n\n{context}"

        model = node.model
        if not model:
            pool_entry = self.agent_pool.get(node.role.value)
            if pool_entry:
                model = pool_entry.model

        timeout = node.timeout
        if timeout is None:
            pool_entry = self.agent_pool.get(node.role.value)
            if pool_entry:
                timeout = pool_entry.timeout

        stdout, code = await invoke_agent(
            node.role.value,  # type: ignore[arg-type]
            task,
            self.project_path,
            model=model or None,
            timeout=float(timeout) if timeout is not None else 600.0,
        )

        if code != 0:
            raise RuntimeError(f"agent {node.role.value} exited with code {code}")

        return stdout

    async def _evaluate_gate(self, node: GateNode) -> Verdict:
        """Evaluate a gate and return a verdict."""
        if self.dry_run:
            return Verdict.proceed()

        if node.evaluator_type == "user":
            return Verdict.proceed()

        if node.evaluator_type == "fn":
            if node.evaluator_command:
                cmd = node.evaluator_command.replace(
                    "{project_path}", shlex.quote(str(self.project_path)),
                )
                try:
                    output = await self._run_shell(cmd)
                    return self._parse_fn_verdict(output, node.id)
                except RuntimeError:
                    return Verdict.halt(reason=f"gate command failed: {cmd}")
            return Verdict.proceed()

        prompt = self._build_gate_prompt(node)
        from factory.agents.runner import invoke_agent

        model = "opus"
        pool_entry = self.agent_pool.get("ceo")
        if pool_entry:
            model = pool_entry.model

        stdout, code = await invoke_agent(
            "ceo",
            prompt,
            self.project_path,
            model=model,
        )

        if code != 0:
            return Verdict.halt(reason=f"CEO gate agent exited with code {code}")

        return self._parse_agent_verdict(stdout, node.id)

    def _build_gate_prompt(self, node: GateNode) -> str:
        """Build the lightweight CEO gate prompt."""
        if node.gate_prompt:
            return node.gate_prompt.replace(
                "{project_path}", str(self.project_path),
            )

        output_files = sorted(node.reads) if node.reads else ["(no specific file)"]
        context = self.node_context.get(node.id, "none")

        reloop_targets: list[str] = []
        for edge in self._edge_index.get(node.id, []):
            if edge.condition == VerdictType.RELOOP:
                reloop_targets.append(edge.target)

        return CEO_GATE_PROMPT.format(
            step_name=node.id,
            workflow_name=self.workflow.name,
            output_file=", ".join(output_files),
            previous_context=context,
            reloop_targets=", ".join(reloop_targets) if reloop_targets else "(use exact node IDs)",
        )

    def _parse_agent_verdict(self, output: str, gate_id: str) -> Verdict:
        """Parse agent output into a Verdict by examining the last non-empty line."""
        import re

        lines = output.strip().splitlines()
        last_line = ""
        for line in reversed(lines):
            if line.strip():
                last_line = line.strip()
                break

        text = last_line.upper()

        if text.startswith("HALT") or re.match(r"^HALT\b", text):
            reason_match = re.search(r'REASON="([^"]+)"', last_line, re.IGNORECASE)
            reason = reason_match.group(1) if reason_match else "gate halted"
            return Verdict.halt(reason=reason)

        if text.startswith("RELOOP") or re.match(r"^RELOOP\b", text):
            target_match = re.search(r'TARGET="([^"]+)"', last_line, re.IGNORECASE)
            feedback_match = re.search(r'FEEDBACK="([^"]+)"', last_line, re.IGNORECASE)
            target = target_match.group(1) if target_match else None

            if target and target not in self.workflow.nodes:
                matches = [nid for nid in self.workflow.nodes if target in nid]
                if len(matches) == 1:
                    target = matches[0]
                else:
                    target = self._next_conditional(gate_id, VerdictType.RELOOP)

            if not target:
                target = self._next_conditional(gate_id, VerdictType.RELOOP)
            if not target:
                return Verdict.halt(reason=f"RELOOP verdict from gate '{gate_id}' missing target and no RELOOP edge defined")
            feedback = feedback_match.group(1) if feedback_match else "needs improvement"
            return Verdict.reloop(target=target, feedback=feedback)

        return Verdict.proceed()

    def _parse_fn_verdict(self, output: str, gate_id: str) -> Verdict:
        """Parse function output into a Verdict."""
        text = output.strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict) and "passed" in data:
                if data["passed"]:
                    return Verdict.proceed()
                return Verdict.halt(
                    reason=f"precheck failed: {data.get('blocking_failures', [])!r}"[:200]
                )
        except (json.JSONDecodeError, TypeError):
            pass

        first_line = text.split("\n")[0].strip().lower()
        if first_line.startswith("pass"):
            return Verdict.proceed()
        if first_line.startswith("fail") or first_line.startswith("revert"):
            return Verdict.halt(reason=f"precheck failed: {text[:200]}")
        if first_line.startswith("reloop"):
            target = self._next_conditional(gate_id, VerdictType.RELOOP)
            raw_line = text.split("\n")[0].strip()
            after_prefix = raw_line.split(":", 1)[1].strip() if ":" in raw_line else ""
            feedback = after_prefix if after_prefix else "fn gate requested reloop"
            if target:
                return Verdict.reloop(target=target, feedback=feedback)
            return Verdict.halt(reason="fn gate returned RELOOP but no RELOOP edge defined")
        return Verdict.proceed()

    async def _run_shell(self, cmd: str) -> str:
        """Run a shell command and return stdout."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_path,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode() if stdout_bytes else ""

        if proc.returncode != 0:
            stderr = stderr_bytes.decode() if stderr_bytes else ""
            raise RuntimeError(
                f"command failed (exit {proc.returncode}): {cmd}\n{stderr[:500]}"
            )

        return stdout

    async def _wait_for_reads(self, node: NodeType) -> None:
        """Wait until all files in node.reads are available in completed_files."""
        if not node.reads:
            return
        poll_interval = 0.1
        max_wait = 60.0
        waited = 0.0
        while True:
            missing = node.reads - self.completed_files
            if not missing:
                return
            if waited >= max_wait:
                self.result.halted = True
                self.result.halt_reason = (
                    f"node '{node.id}' timed out waiting for reads: {sorted(missing)}"
                )
                return
            log.debug(
                "node.waiting_for_reads",
                node=node.id,
                missing=sorted(missing),
                waited_s=round(waited, 1),
            )
            await asyncio.sleep(poll_interval)
            waited += poll_interval

    def _next_unconditional(self, node_id: str) -> str | None:
        """Find the next node via unconditional edge."""
        for edge in self._edge_index.get(node_id, []):
            if edge.condition is None:
                return edge.target
        return None

    def _next_conditional(self, node_id: str, verdict_type: VerdictType) -> str | None:
        """Find the next node via conditional edge matching the verdict."""
        for edge in self._edge_index.get(node_id, []):
            if edge.condition == verdict_type:
                return edge.target
        return None

    def _emit(self, event_type: str, event: Any) -> None:
        """Emit a workflow event."""
        self.result.events.append({"type": event_type, **event.model_dump(mode="python")})
        try:
            emit_workflow_event(self.project_path, event_type, event)
        except Exception:
            log.debug("event_emission_failed", event_type=event_type)


def _parse_hypotheses(strategy_file: Path) -> list[str]:
    """Extract individual hypotheses from the strategist's current.md output."""
    text = strategy_file.read_text()
    hypotheses: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Hypothesis") or stripped.startswith("### Hypothesis"):
            if current:
                hypotheses.append("\n".join(current).strip())
                current = []
            current.append(stripped)
        elif stripped.startswith("## ") and current:
            hypotheses.append("\n".join(current).strip())
            current = []
        elif current:
            current.append(line)

    if current:
        hypotheses.append("\n".join(current).strip())

    if not hypotheses:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- **") or stripped.startswith("1. **"):
                hypotheses.append(stripped.lstrip("- 0123456789.").strip())

    return hypotheses


def _collect_subgraph_nodes(
    workflow: Workflow,
    entry: str,
    exit_node: str,
) -> set[str]:
    """Collect all node IDs on paths from entry to exit_node (inclusive)."""
    edges_by_source: dict[str, list[str]] = {}
    for edge in workflow.edges:
        edges_by_source.setdefault(edge.source, []).append(edge.target)

    # BFS from entry, stop at exit_node
    visited: set[str] = set()
    queue = [entry]
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        if nid == exit_node:
            continue
        for target in edges_by_source.get(nid, []):
            if target not in visited:
                queue.append(target)

    return visited
