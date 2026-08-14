"""Factory node behavior executed by the LangGraph workflow runtime."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command, Interrupt

from factory.workflow.events import WorkflowEvent, emit_workflow_event
from factory.workflow.langgraph import (
    FactoryRunState,
    collect_subgraph_nodes,
    compile_langgraph,
    initial_state,
)
from factory.workflow.primitives import (
    AgentConfig,
    AgentNode,
    FnNode,
    ForkNode,
    GateNode,
    JoinNode,
    LLMNode,
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


@dataclass
class ExecutionResult:
    """External result returned by a LangGraph workflow invocation."""

    success: bool = False
    completed: bool = False
    halted: bool = False
    halt_reason: str = ""
    interrupted: bool = False
    thread_id: str = ""
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    nodes_executed: int = 0
    completed_nodes: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    completed_files: set[str] = field(default_factory=set)
    node_outputs: dict[str, str] = field(default_factory=dict)
    duration_ms: float = 0.0
    state: dict[str, Any] = field(default_factory=dict)


class WorkflowExecutor:
    """Compatibility facade over the canonical LangGraph workflow runtime."""

    def __init__(
        self,
        workflow: Workflow,
        project_path: Path,
        agent_pool: dict[str, AgentConfig] | None = None,
        *,
        dry_run: bool = False,
        auto_approve: bool = False,
        interactive: bool = False,
        thread_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.workflow = workflow
        self.project_path = project_path
        self.agent_pool = agent_pool or {}
        self.dry_run = dry_run
        self.auto_approve = auto_approve
        self.interactive = interactive
        self.run_id = thread_id or uuid.uuid4().hex[:12]
        self.checkpoint_path = checkpoint_path or (
            project_path / ".factory" / "langgraph" / "checkpoints.sqlite"
        )
        self.result = ExecutionResult(thread_id=self.run_id)
        self.completed_files: set[str] = set()
        self.node_context: dict[str, str] = {}
        self.iteration_counts: dict[tuple[str, str], int] = {}
        self._edge_index = {
            node_id: [edge for edge in workflow.edges if edge.source == node_id]
            for node_id in workflow.nodes
        }

    @property
    def config(self) -> RunnableConfig:
        """LangGraph thread configuration for this workflow run."""
        return {"configurable": {"thread_id": self.run_id}}

    async def execute(self) -> ExecutionResult:
        """Start the workflow and run until completion or an interrupt."""
        self._write_thread_manifest()
        state = initial_state(self.workflow, str(self.project_path), self.run_id)
        result = await self._invoke(state)
        if result.completed:
            self._log_timing_summary(result)
        return result

    async def resume(self, value: Any) -> ExecutionResult:
        """Resume this persisted workflow thread after an interrupt."""
        result = await self._invoke(Command(resume=value))
        if result.completed:
            self._log_timing_summary(result)
        return result

    async def inspect(self) -> ExecutionResult:
        """Read the latest persisted state without advancing the graph."""
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
            await saver.setup()
            graph = compile_langgraph(self.workflow, self, checkpointer=saver)
            snapshot = await graph.aget_state(self.config)
        return self._sync_result(snapshot.values, snapshot.interrupts)

    @classmethod
    def from_thread(
        cls,
        project_path: Path,
        thread_id: str,
        agent_pool: dict[str, AgentConfig] | None = None,
        *,
        checkpoint_path: Path | None = None,
    ) -> WorkflowExecutor:
        """Reconstruct the exact workflow/runtime settings recorded for a thread."""
        resolved_checkpoint = checkpoint_path or (
            project_path / ".factory" / "langgraph" / "checkpoints.sqlite"
        )
        manifest_path = resolved_checkpoint.parent / "threads" / f"{thread_id}.json"
        manifest = json.loads(manifest_path.read_text())
        workflow = Workflow.model_validate_json(str(manifest["workflow_json"]))
        return cls(
            workflow,
            project_path,
            agent_pool=agent_pool,
            dry_run=bool(manifest["dry_run"]),
            auto_approve=bool(manifest["auto_approve"]),
            interactive=bool(manifest["interactive"]),
            thread_id=thread_id,
            checkpoint_path=resolved_checkpoint,
        )

    async def _invoke(self, graph_input: FactoryRunState | Command[Any]) -> ExecutionResult:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
            await saver.setup()
            graph = compile_langgraph(self.workflow, self, checkpointer=saver)
            await graph.ainvoke(graph_input, self.config)
            snapshot = await graph.aget_state(self.config)
        return self._sync_result(snapshot.values, snapshot.interrupts)

    def _sync_result(
        self,
        values: dict[str, Any] | Any,
        interrupts: tuple[Interrupt, ...],
    ) -> ExecutionResult:
        state = cast(FactoryRunState, values or {})
        events = list(state.get("events", []))
        interrupted = bool(interrupts)
        halted = bool(state.get("halted", False))
        completed = any(
            event.get("type") in {"workflow.completed", "workflow.halted"}
            for event in events
        )
        duration_ms = 0.0
        for event in reversed(events):
            if event.get("type") == "workflow.completed":
                duration_ms = float(event.get("duration_ms", 0.0))
                break

        self.completed_files = set(state.get("completed_files", []))
        self.node_context = {
            key: str(value) for key, value in state.get("node_context", {}).items()
        }
        self.iteration_counts = {}
        for key, value in state.get("iteration_counts", {}).items():
            gate_id, target_id = key.split("->", 1)
            self.iteration_counts[(gate_id, target_id)] = int(value)
        self.result = ExecutionResult(
            success=completed and not interrupted and not halted,
            completed=completed,
            halted=halted,
            halt_reason=str(state.get("halt_reason", "")),
            interrupted=interrupted,
            thread_id=self.run_id,
            interrupts=[{"id": item.id, "value": item.value} for item in interrupts],
            nodes_executed=int(state.get("nodes_executed", 0)),
            completed_nodes=set(state.get("completed_nodes", [])),
            events=events,
            completed_files=self.completed_files,
            node_outputs={
                key: str(value) for key, value in state.get("node_outputs", {}).items()
            },
            duration_ms=duration_ms,
            state=dict(state),
        )
        return self.result

    def _log_timing_summary(self, result: ExecutionResult) -> None:
        """Log timing data projected from durable node-completion events."""
        node_timings = [
            {
                "id": event.get("node_id", ""),
                "type": event.get("node_type", ""),
                "duration_ms": round(float(event["duration_ms"]), 1),
            }
            for event in result.events
            if event.get("type") == "node.completed" and "duration_ms" in event
        ]
        node_timings.sort(key=lambda entry: float(entry["duration_ms"]), reverse=True)
        node_total_ms = sum(float(entry["duration_ms"]) for entry in node_timings)
        log.info(
            "workflow.timing_summary",
            workflow=self.workflow.name,
            run_id=self.run_id,
            total_ms=round(result.duration_ms, 1),
            node_count=len(node_timings),
            nodes=node_timings,
            overhead_ms=round(result.duration_ms - node_total_ms, 1),
        )

    async def run_node(self, node: NodeType, state: FactoryRunState) -> str:
        """Run one Factory domain node with a durable completion receipt."""
        attempt = int(state["node_attempts"].get(node.id, 0)) + 1
        receipt_path = self._receipt_path(node.id, attempt)
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt["status"] == "completed":
                return str(receipt["output"])
            raise RuntimeError(
                f"operation {receipt['operation_id']} has an ambiguous prior attempt; "
                "inspect external effects before retrying"
            )

        operation_id = f"{self.run_id}:{node.id}:{attempt}"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({
            "operation_id": operation_id,
            "node_id": node.id,
            "attempt": attempt,
            "status": "started",
        }, indent=2))
        output = await self._run_node(node, state)
        receipt_path.write_text(json.dumps({
            "operation_id": operation_id,
            "node_id": node.id,
            "attempt": attempt,
            "status": "completed",
            "output": output,
        }, indent=2))
        return output

    async def evaluate_gate(self, node: GateNode, state: FactoryRunState) -> Verdict:
        """Evaluate one gate through the Factory-owned evaluator behavior."""
        self.node_context = {
            key: str(value) for key, value in state["node_context"].items()
        }
        return await self._evaluate_gate(node)

    def parse_gate_submission(self, node: GateNode, output: str) -> Verdict:
        """Parse an interactive gate response into the Factory verdict algebra."""
        return self._parse_agent_verdict(output, node.id)

    def accept_submission(self, node: NodeType, output: str) -> str:
        """Persist externally executed agent output at its declared artifact boundary."""
        if isinstance(node, AgentNode):
            for write_path in node.writes:
                artifact = self.project_path / write_path
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(output)
            self._validate_agent_artifacts(node)
        return output

    def emit_event(self, event_type: str, event: WorkflowEvent) -> None:
        """Write one graph event to the project's append-only event log."""
        emit_workflow_event(self.project_path, event_type, event)

    async def _run_node(self, node: NodeType, state: FactoryRunState) -> str:
        if self.dry_run:
            if isinstance(node, SelectionNode):
                return json.dumps({
                    "strategy": node.strategy,
                    "winner": None,
                    "reason": "dry-run",
                })
            if isinstance(node, SubgraphForkNode):
                return await self._run_subgraph_fork(node)
            return f"[dry-run] {node.id} executed"

        if isinstance(node, Study):
            return await self._run_study(node)
        if isinstance(node, AgentNode):
            return await self._run_agent(node, state)
        if isinstance(node, LLMNode):
            return await self._run_llm(node, state)
        if isinstance(node, SelectionNode):
            return await self._run_selection(node, state)
        if isinstance(node, SubgraphForkNode):
            return await self._run_subgraph_fork(node)
        if isinstance(node, FnNode):
            return await self._run_fn(node)
        if isinstance(node, ForkNode):
            return json.dumps({"targets": node.targets})
        if isinstance(node, JoinNode):
            return json.dumps({"sources": node.sources})
        raise TypeError(f"unsupported workflow node: {type(node).__name__}")

    async def _run_study(self, node: Study) -> str:
        cmd = node.command or f"factory study {shlex.quote(str(self.project_path))}"
        if node.focus and "--focus" not in cmd:
            cmd += f" --focus {shlex.quote(node.focus)}"
        return await self._run_shell(self._resolve_command(cmd))

    async def _run_fn(self, node: FnNode) -> str:
        if not node.command:
            return ""
        return await self._run_shell(self._resolve_command(node.command))

    async def _run_agent(self, node: AgentNode, state: FactoryRunState) -> str:
        from factory.agents.runner import invoke_agent

        task = node.prompt_template
        context = str(state["node_context"].get(node.id, ""))
        if context:
            task = f"{task}\n\n{context}"

        pool_entry = self.agent_pool.get(node.role.value)
        model = node.model or (pool_entry.model if pool_entry else "")
        timeout = node.timeout or (pool_entry.timeout if pool_entry else 600)
        stdout, code = await invoke_agent(
            node.role.value,  # type: ignore[arg-type]
            task,
            self.project_path,
            model=model or None,
            timeout=float(timeout),
        )
        if code != 0:
            raise RuntimeError(f"agent {node.role.value} exited with code {code}")
        self._validate_agent_artifacts(node)
        return stdout

    async def _run_llm(self, node: LLMNode, state: FactoryRunState) -> str:
        from factory.workflow.llm_loop import run_llm_loop

        context_parts = [
            (self.project_path / path).read_text()
            for path in sorted(node.reads)
            if (self.project_path / path).exists()
        ]
        gate_context = str(state["node_context"].get(node.id, ""))
        if gate_context:
            context_parts.append(gate_context)
        output = await asyncio.wait_for(
            run_llm_loop(
                node,
                self.project_path,
                instance_context="\n\n".join(context_parts),
            ),
            timeout=float(node.timeout),
        )
        output_path = self.project_path / ".factory" / "reviews" / "builder-latest.md"
        if node.writes:
            output_path = self.project_path / sorted(node.writes)[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
        return output

    async def _run_subgraph_fork(self, node: SubgraphForkNode) -> str:
        from factory.worktree import create_experiment_worktree

        if self.dry_run:
            base_commit = "0" * 40
        else:
            resolved = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                check=True,
            )
            base_commit = resolved.stdout.strip()

        strategy_file = self.project_path / ".factory" / "strategy" / "current.md"
        hypotheses = _parse_hypotheses(strategy_file) if strategy_file.exists() else []
        branch_count = min(len(hypotheses), node.parallelism) if hypotheses else node.parallelism
        branch_count = max(branch_count, 1)
        subgraph_ids = collect_subgraph_nodes(
            self.workflow,
            node.subgraph_entry,
            node.subgraph_exit,
        )
        sub_workflow = self.workflow.subgraph(
            subgraph_ids,
            name=f"{self.workflow.name}__branch",
            start_node=node.subgraph_entry,
        )

        async def run_branch(index: int) -> dict[str, Any]:
            from factory.store import ExperimentStore

            hypothesis = (
                hypotheses[index] if index < len(hypotheses) else f"Hypothesis {index + 1}"
            )
            if self.dry_run:
                worktree_path = self.project_path
                branch = f"factory/exp-dry-{index}"
                experiment_id = index + 1
            else:
                store = ExperimentStore(self.project_path)
                experiment_id = await store.begin(hypothesis)
                worktree_path, branch = create_experiment_worktree(
                    self.project_path,
                    experiment_id,
                    base_commit,
                )

            branch_executor = WorkflowExecutor(
                sub_workflow.model_copy(deep=True),
                worktree_path,
                agent_pool=self.agent_pool,
                dry_run=self.dry_run,
                auto_approve=self.auto_approve,
                checkpoint_path=(
                    self.checkpoint_path.parent
                    / f"{self.run_id}-{node.id}-{index}-checkpoints.sqlite"
                ),
            )
            branch_result = await branch_executor.execute()
            return {
                "exp_id": experiment_id,
                "hypothesis": hypothesis,
                "worktree_path": str(worktree_path),
                "branch": branch,
                "success": branch_result.success,
                "halted": branch_result.halted,
                "halt_reason": branch_result.halt_reason,
                "nodes_executed": branch_result.nodes_executed,
                "node_outputs": branch_result.node_outputs,
            }

        semaphore = asyncio.Semaphore(node.parallelism)

        async def throttled_branch(index: int) -> dict[str, Any]:
            async with semaphore:
                return await run_branch(index)

        gathered = await asyncio.gather(
            *(throttled_branch(index) for index in range(branch_count)),
            return_exceptions=True,
        )
        branch_results = [
            (
                {
                    "success": False,
                    "halted": True,
                    "halt_reason": str(item),
                }
                if isinstance(item, BaseException)
                else item
            )
            for item in gathered
        ]
        output = json.dumps(branch_results)
        for write_path in node.writes:
            target = self.project_path / write_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(output)
        return output

    async def _run_selection(self, node: SelectionNode, state: FactoryRunState) -> str:
        from factory.models import ExperimentRecord
        from factory.store import ExperimentStore
        from factory.worktree import remove_worktree

        branches = self._find_branch_results(state["node_outputs"])
        successful = [branch for branch in branches if branch.get("success")]
        if not successful:
            raise RuntimeError("all parallel experiment branches failed")

        for branch in successful:
            eval_file = Path(str(branch["worktree_path"])) / ".factory" / "last_eval.json"
            score = 0.0
            if eval_file.exists():
                data = json.loads(eval_file.read_text())
                score = float(data.get("total", data.get("score", 0.0)))
            branch["score"] = score

        best = max(successful, key=lambda branch: float(branch.get("score", 0.0)))
        subprocess.run(
            [
                "git",
                "merge",
                str(best["branch"]),
                "--no-edit",
                "-m",
                f"Merge parallel experiment winner (exp {best['exp_id']})",
            ],
            cwd=self.project_path,
            check=True,
            capture_output=True,
        )

        store = ExperimentStore(self.project_path)
        for branch in branches:
            experiment_id = branch.get("exp_id")
            if branch is not best and experiment_id is not None:
                record = ExperimentRecord(
                    id=int(experiment_id),
                    timestamp=datetime.now(tz=timezone.utc),
                    hypothesis=str(branch.get("hypothesis", "")),
                    change_summary=f"superseded by experiment {best['exp_id']}",
                    issue_number=None,
                    pr_number=None,
                    score_before=None,
                    score_after=float(branch.get("score", 0.0)),
                    delta=None,
                    verdict="superseded",
                    cost_usd=None,
                    notes="",
                )
                await store.finalize(int(experiment_id), record)

            worktree_path = Path(str(branch.get("worktree_path", "")))
            branch_name = str(branch.get("branch", ""))
            if worktree_path.exists() and branch_name:
                remove_worktree(self.project_path, worktree_path, branch_name)

        selection = {
            "strategy": node.strategy,
            "winner_exp_id": best["exp_id"],
            "winner_score": best.get("score", 0.0),
            "winner_hypothesis": best.get("hypothesis", ""),
            "total_branches": len(branches),
            "successful_branches": len(successful),
        }
        output = json.dumps(selection)
        for write_path in node.writes:
            target = self.project_path / write_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(output)
        return output

    async def _evaluate_gate(self, node: GateNode) -> Verdict:
        if self.dry_run:
            return Verdict.proceed()
        if node.evaluator_type == "user":
            log.info("gate.auto_approved", gate_id=node.id, workflow=self.workflow.name)
            return Verdict.proceed()
        if node.evaluator_type == "fn":
            if not node.evaluator_command:
                return Verdict.proceed()
            cmd = self._resolve_command(node.evaluator_command)
            stdout, stderr, code = await self._run_shell_result(cmd)
            if code != 0:
                reason = stderr.strip() or stdout.strip() or f"gate command failed: {cmd}"
                return Verdict.halt(reason=reason[:500])
            return self._parse_fn_verdict(stdout, node.id)

        from factory.agents.runner import invoke_agent

        pool_entry = self.agent_pool.get("ceo")
        stdout, code = await invoke_agent(
            "ceo",
            self._build_gate_prompt(node),
            self.project_path,
            model=pool_entry.model if pool_entry else "opus",
        )
        if code != 0:
            raise RuntimeError(f"CEO gate agent exited with code {code}")
        return self._parse_agent_verdict(stdout, node.id)

    def _build_gate_prompt(self, node: GateNode) -> str:
        if node.gate_prompt:
            return node.gate_prompt.replace("{project_path}", str(self.project_path))
        reloop_targets = [
            edge.target
            for edge in self._edge_index.get(node.id, [])
            if edge.condition == VerdictType.RELOOP
        ]
        return CEO_GATE_PROMPT.format(
            step_name=node.id,
            workflow_name=self.workflow.name,
            output_file=", ".join(sorted(node.reads)) or "(no specific file)",
            previous_context=self.node_context.get(node.id, "none"),
            reloop_targets=", ".join(reloop_targets) or "(use exact node IDs)",
        )

    def _parse_agent_verdict(self, output: str, gate_id: str) -> Verdict:
        last_line = next(
            (line.strip() for line in reversed(output.strip().splitlines()) if line.strip()),
            "",
        )
        text = last_line.upper()
        if text.startswith("HALT"):
            reason_match = re.search(r'REASON="([^"]+)"', last_line, re.IGNORECASE)
            return Verdict.halt(
                reason=reason_match.group(1) if reason_match else "gate halted",
            )
        if text.startswith(("RELOOP", "RETRY")):
            target_match = re.search(
                r'TARGET=(?:"([^"]+)"|(\S+))',
                last_line,
                re.IGNORECASE,
            )
            feedback_match = re.search(r'FEEDBACK="([^"]+)"', last_line, re.IGNORECASE)
            target = None
            if target_match:
                target = target_match.group(1) or target_match.group(2)
            if target not in self.workflow.nodes:
                target = self._next_conditional(gate_id, VerdictType.RELOOP)
            if target is None:
                return Verdict.halt(
                    reason=f"RELOOP verdict from gate '{gate_id}' has no target",
                )
            feedback = feedback_match.group(1) if feedback_match else "needs improvement"
            return Verdict.reloop(target=target, feedback=feedback)
        return Verdict.proceed()

    def _parse_fn_verdict(self, output: str, gate_id: str) -> Verdict:
        text = output.strip()
        if text.startswith("{"):
            data = json.loads(text)
            if isinstance(data, dict) and "passed" in data:
                if data["passed"]:
                    return Verdict.proceed()
                return Verdict.halt(
                    reason=f"precheck failed: {data.get('blocking_failures', [])!r}"[:200],
                )
        first_line = text.split("\n", 1)[0].strip().lower()
        if first_line.startswith("pass"):
            return Verdict.proceed()
        if first_line.startswith(("fail", "revert", "halt")):
            return Verdict.halt(reason=f"precheck failed: {text[:200]}")
        if first_line.startswith(("reloop", "retry")):
            target = self._next_conditional(gate_id, VerdictType.RELOOP)
            feedback = first_line.split(":", 1)[1].strip() if ":" in first_line else ""
            if target:
                return Verdict.reloop(
                    target=target,
                    feedback=feedback or "fn gate requested reloop",
                )
            return Verdict.halt(reason="fn gate returned RELOOP but no RELOOP edge defined")
        return Verdict.proceed()

    async def _run_shell(self, cmd: str) -> str:
        stdout, stderr, code = await self._run_shell_result(cmd)
        if code != 0:
            raise RuntimeError(f"command failed (exit {code}): {cmd}\n{stderr[:500]}")
        return stdout

    async def _run_shell_result(self, cmd: str) -> tuple[str, str, int]:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_path,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return (
            stdout_bytes.decode() if stdout_bytes else "",
            stderr_bytes.decode() if stderr_bytes else "",
            int(process.returncode or 0),
        )

    def _resolve_command(self, command: str) -> str:
        return command.replace("{project_path}", shlex.quote(str(self.project_path)))

    def _validate_agent_artifacts(self, node: AgentNode) -> None:
        from factory.workflow.primitives import ArtifactCheck

        checks = node.post_checks or [
            ArtifactCheck(path=path) for path in sorted(node.writes)
        ]
        for check in checks:
            artifact = self.project_path / check.path
            if check.must_exist and not artifact.is_file():
                raise RuntimeError(f"artifact verification failed: {check.path} missing")
            if artifact.is_file() and artifact.stat().st_size < check.min_size:
                raise RuntimeError(
                    f"artifact verification failed: {check.path} smaller than {check.min_size}",
                )
            if artifact.is_file() and check.must_contain:
                content = artifact.read_text()
                if not any(sentinel in content for sentinel in check.must_contain):
                    raise RuntimeError(
                        f"artifact verification failed: {check.path} missing sentinel",
                    )

    def _next_conditional(self, node_id: str, verdict_type: VerdictType) -> str | None:
        for edge in self._edge_index.get(node_id, []):
            if edge.condition == verdict_type:
                return edge.target
        return None

    def _receipt_path(self, node_id: str, attempt: int) -> Path:
        return (
            self.project_path
            / ".factory"
            / "langgraph"
            / "receipts"
            / self.run_id
            / f"{node_id}-{attempt}.json"
        )

    def _write_thread_manifest(self) -> None:
        manifest_path = self.checkpoint_path.parent / "threads" / f"{self.run_id}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({
            "thread_id": self.run_id,
            "workflow_name": self.workflow.name,
            "workflow_json": self.workflow.model_dump_json(),
            "project_path": str(self.project_path),
            "dry_run": self.dry_run,
            "auto_approve": self.auto_approve,
            "interactive": self.interactive,
        }, indent=2))

    @staticmethod
    def _find_branch_results(outputs: dict[str, Any]) -> list[dict[str, Any]]:
        for output in outputs.values():
            value = json.loads(str(output))
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if "exp_id" in value[0]:
                    return value
        return []


def _parse_hypotheses(strategy_file: Path) -> list[str]:
    """Parse hypothesis sections from a strategy markdown file."""
    text = strategy_file.read_text()
    hypotheses: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("## Hypothesis", "### Hypothesis")):
            if current:
                hypotheses.append("\n".join(current).strip())
            current = [stripped]
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
            if stripped.startswith("- **") or re.match(r"^\d+\. \*\*", stripped):
                hypotheses.append(stripped.lstrip("- 0123456789.").strip())
    return hypotheses


def _collect_subgraph_nodes(workflow: Workflow, entry: str, exit_node: str) -> set[str]:
    """Backward-compatible alias for the compiler's subgraph collector."""
    return collect_subgraph_nodes(workflow, entry, exit_node)
