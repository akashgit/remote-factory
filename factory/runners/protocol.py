"""Runner protocol — re-exports AgentRunner as the canonical interface.

All runners now extend AgentRunner (factory/runners/abstraction.py).
This module exists for backward compatibility — import AgentRunner directly
from factory.runners.abstraction instead.
"""

from factory.runners.abstraction import AgentRunner as Runner

__all__ = ["Runner"]
