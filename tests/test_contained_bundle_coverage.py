"""The one branch in `bundle` that only fires when the cluster is unreachable.

`render_bundle` has to produce YAML with no cluster in reach — that is the whole point of `bundle`,
which a user hands to someone who owns a namespace they cannot touch. So the current-namespace
lookup swallows everything and falls back to None, and that swallow is the branch under test.
"""

from __future__ import annotations

from unittest.mock import patch

from factory.contained import bundle


def test_current_namespace_lookup_swallows_every_failure() -> None:
    """A broken or absent CLI must degrade to "no namespace known", never raise into rendering."""
    with patch("factory.contained.k8s.current_namespace", side_effect=RuntimeError("no cli")):
        assert bundle._safe_current_namespace() is None
