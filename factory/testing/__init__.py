"""Test infrastructure for workflow behavioral tests.

Provides contract-enforcing test doubles for the workflow executor:

- ``FakeAgent``: callable matching the ``agent_fn`` signature that enforces
  ``node.writes`` contracts and maintains a spy call log.
- ``DummyTask``: ``Task`` subclass with fixed instances and deterministic
  ``verify()`` for outer-loop behavioral tests.
- ``CallRecord``: dataclass capturing a single FakeAgent invocation.
"""

from factory.testing.fake_agent import CallRecord, FakeAgent
from factory.testing.dummy_task import DummyTask

__all__ = ["CallRecord", "DummyTask", "FakeAgent"]
