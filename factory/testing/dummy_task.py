"""DummyTask — a Task subclass with fixed instances and deterministic verify().

Designed for testing the outer loop and DataNode execution paths without
external dependencies.  Exercises the real ``Task`` protocol with controlled,
predictable data.

Usage::

    from factory.testing import DummyTask

    task = DummyTask()
    for inst in task.instances():
        task.setup(inst, workspace)
        prompt = task.prompt(inst)
        result = task.verify(inst, workspace)
        assert result.passed
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Literal

from factory.task import Task, TaskDefinition, TaskInstance, VerifyResult


class DummyTask(Task):
    """Task with fixed instances and deterministic verify().

    Parameters
    ----------
    instances_data:
        List of dicts with at least an ``"id"`` key.  Defaults to three
        items: ``item-1``, ``item-2``, ``item-3``.
    """

    def __init__(
        self,
        instances_data: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(TaskDefinition(name="dummy-task"))
        self._instances = instances_data or [
            {"id": "item-1"},
            {"id": "item-2"},
            {"id": "item-3"},
        ]

    def instances(
        self, split: Literal["train", "val", "all"] = "all",
    ) -> Iterator[TaskInstance]:
        """Yield TaskInstance for each configured item.

        The *split* parameter is accepted for compatibility with the parent
        ``Task.instances()`` signature but is ignored — DummyTask always
        returns all instances regardless of the requested split.
        """
        for item in self._instances:
            yield TaskInstance(id=item["id"])

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        """No-op setup."""

    def prompt(self, instance: TaskInstance) -> str:
        """Return a deterministic prompt."""
        return f"Process instance {instance.id}"

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        """Always pass with score 1.0."""
        return VerifyResult(passed=True, score=1.0)
