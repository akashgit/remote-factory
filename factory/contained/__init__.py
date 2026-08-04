"""Everything `factory contained` needs that is not specific to one runtime's CLI.

Podman command composition lives in `factory/podman.py` and the cluster's in
`factory/contained/k8s.py`; this package holds the parts that are the same regardless of which
runtime a command lands in — workspace materialization, path translation, provenance checks,
credential resolution, prerequisites, and lifecycle.

Deliberately empty of imports: `factory.podman` imports `factory.contained.provenance`, and a
package `__init__` that reached back into `factory.podman` would make that a cycle.
"""

from __future__ import annotations
