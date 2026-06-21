"""Deterministic async graph walker implementing formal execution semantics."""

from __future__ import annotations

import asyncio
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
    Study,
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
- **Reloop(target, feedback)**: the output needs improvement. Specify which step to return to and what feedback to provide.
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

        if isinstance(node, ForkNode):
            await self._execute_fork(node)
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
                self.result.halted = True
                self.result.halt_reason = f"fork branch '{target_id}' failed: {exc}"

        await asyncio.gather(*(run_branch(t) for t in node.targets))

        if self.result.halted:
            return

        # Follow the edge from the first branch target to find the join/next node
        next_id = self._next_unconditional(node.targets[0]) if node.targets else None
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
        cmd = f"factory study {self.project_path}"
        if node.focus:
            cmd += f' --focus "{node.focus}"'
        return await self._run_shell(cmd)

    async def _run_fn(self, node: FnNode) -> str:
        """Run a FnNode's shell command."""
        if not node.command:
            return ""
        cmd = node.command.replace("{project_path}", str(self.project_path))
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

        stdout, code = await invoke_agent(
            node.role.value,  # type: ignore[arg-type]
            task,
            self.project_path,
            model=model or None,
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
                    "{project_path}", str(self.project_path),
                )
                try:
                    output = await self._run_shell(cmd)
                    return self._parse_fn_verdict(output)
                except RuntimeError:
                    return Verdict.halt(reason=f"gate command failed: {node.evaluator_command}")
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

        return CEO_GATE_PROMPT.format(
            step_name=node.id,
            workflow_name=self.workflow.name,
            output_file=", ".join(output_files),
            previous_context=context,
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
            target = target_match.group(1) if target_match else gate_id
            feedback = feedback_match.group(1) if feedback_match else "needs improvement"
            return Verdict.reloop(target=target, feedback=feedback)

        return Verdict.proceed()

    def _parse_fn_verdict(self, output: str) -> Verdict:
        """Parse function output into a Verdict."""
        text = output.strip().lower()
        if "fail" in text or "revert" in text:
            return Verdict.halt(reason=f"precheck failed: {output.strip()[:200]}")
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
