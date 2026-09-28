"""Contract-enforcing fake agent for behavioral workflow tests.

``FakeAgent`` is a callable matching the ``agent_fn`` signature used by
``WorkflowExecutor``.  It enforces the ``writes`` contract declared on
``AgentNode`` instances and maintains a spy call log for assertions.

Usage::

    from factory.testing import FakeAgent

    agent = FakeAgent(workflow)
    executor = WorkflowExecutor(workflow, project_path, agent_fn=agent, validate=False)
    result = await executor.execute()

    assert agent.call_count == 3
    agent.assert_called("builder")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.workflow.primitives import AgentNode, Workflow


@dataclass
class CallRecord:
    """Record of a single FakeAgent invocation."""

    role: str
    task: str
    project_path: str
    node_id: str | None
    result: tuple[str, int]


class FakeAgent:
    """Contract-enforcing fake agent for behavioral workflow tests.

    Parameters
    ----------
    workflow:
        The workflow being tested — used to look up ``AgentNode.writes``
        when ``node_id`` is provided.
    behavior:
        Callable ``(role, task, project_path, **kw) -> (stdout, exit_code)``
        or None for the default ``("ok", 0)`` response.
    violate_writes:
        When True, skip writing the node's declared ``writes`` files.
        Useful for negative/failure testing.
    """

    # Signal to WorkflowExecutor._run_agent() that this callable manages
    # its own file writes.  When the executor sees this attribute it skips
    # the default "write stdout to every node.writes path" behaviour,
    # allowing violate_writes=True to genuinely simulate missing outputs.
    manages_writes: bool = True

    def __init__(
        self,
        workflow: Workflow,
        *,
        behavior: Any | None = None,
        violate_writes: bool = False,
    ) -> None:
        self.workflow = workflow
        self._behavior = behavior
        self._violate_writes = violate_writes
        self.calls: list[CallRecord] = []

    async def __call__(
        self,
        role: str,
        task: str,
        project_path: Path | str,
        *,
        model: str | None = None,
        timeout: float = 600.0,
        node_id: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, int]:
        """Invoke the fake agent, enforcing writes contract."""
        proj = Path(project_path)

        # Determine response
        if self._behavior is not None:
            if callable(self._behavior):
                result = self._behavior(role, task, proj, **kwargs)
                # Support both sync and async behaviors
                import asyncio
                if asyncio.iscoroutine(result):
                    result = await result
            else:
                result = ("ok", 0)
        else:
            result = ("ok", 0)

        stdout, code = result

        # Record the call
        record = CallRecord(
            role=role,
            task=task,
            project_path=str(project_path),
            node_id=node_id,
            result=(stdout, code),
        )
        self.calls.append(record)

        # Enforce writes contract: write exactly the node's declared writes
        if node_id is not None and not self._violate_writes:
            node = self.workflow.nodes.get(node_id)
            if isinstance(node, AgentNode):
                for wpath in node.writes:
                    fpath = proj / wpath
                    fpath.parent.mkdir(parents=True, exist_ok=True)
                    fpath.write_text(stdout)

        return stdout, code

    # ── Assertion helpers ───────────────────────────────────────

    @property
    def call_count(self) -> int:
        """Total number of invocations."""
        return len(self.calls)

    def assert_called(self, role: str) -> None:
        """Assert the agent was called at least once with the given role."""
        if not any(c.role == role for c in self.calls):
            called_roles = sorted({c.role for c in self.calls})
            raise AssertionError(
                f"FakeAgent was never called with role '{role}'. "
                f"Called roles: {called_roles}"
            )

    def assert_call_count(self, n: int) -> None:
        """Assert the agent was called exactly n times."""
        if self.call_count != n:
            raise AssertionError(
                f"Expected {n} calls, got {self.call_count}"
            )

    def get_calls_for_role(self, role: str) -> list[CallRecord]:
        """Return all call records for a specific role."""
        return [c for c in self.calls if c.role == role]
