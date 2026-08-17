"""One error type for "stop before provisioning anything, and say why".

A half-materialized run is worse than none: reporting and stopping means the next attempt starts
clean instead of layering on top of a workspace or plan already known bad. Every module that can
decide a run must not start raises this, and `cmd_contained` is the single place that turns it into
a message and an exit code.
"""

from __future__ import annotations


class ContainedError(RuntimeError):
    """A contained run cannot proceed; the message names the cause and, where possible, the fix."""
