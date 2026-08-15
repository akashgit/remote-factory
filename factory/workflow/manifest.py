"""Plugin manifest validation and namespace enforcement for workflow plugins."""

from __future__ import annotations

import warnings
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, model_validator

log = structlog.get_logger()


class WorkflowManifest(BaseModel):
    """Strict manifest for workflow plugins."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str
    schema_version: int = 1
    min_factory_version: str | None = None
    capabilities: list[Literal["agent_only", "shell_exec"]] = []
    author: str = ""
    url: str = ""

    @model_validator(mode="after")
    def _validate_name(self) -> WorkflowManifest:
        if not self.name:
            raise ValueError("manifest name must be non-empty")
        return self


def validate_namespace(name: str, source: str) -> list[str]:
    """Validate that a workflow name follows namespace conventions.

    Built-in, user, and project sources may use bare names.
    Entry-point (plugin) sources must use 'prefix:name' format.

    Returns a list of issues (empty = valid).
    """
    issues: list[str] = []
    if source == "entry_point":
        if ":" not in name:
            issues.append(
                f"plugin workflow '{name}' must use namespaced format 'prefix:name'"
            )
        else:
            prefix, _, suffix = name.partition(":")
            if not prefix or not suffix:
                issues.append(
                    f"plugin workflow '{name}' has invalid namespace — "
                    "both prefix and name must be non-empty"
                )
    return issues


def check_version_compatibility(manifest: WorkflowManifest) -> list[str]:
    """Check if the manifest's min_factory_version is compatible.

    Returns a list of issues (empty = compatible).
    """
    if not manifest.min_factory_version:
        return []

    issues: list[str] = []
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        current = _get_factory_version()
        spec = SpecifierSet(f">={manifest.min_factory_version}")
        if Version(current) not in spec:
            issues.append(
                f"workflow '{manifest.name}' requires factory >={manifest.min_factory_version}, "
                f"current is {current}"
            )
    except ImportError:
        log.warning(
            "manifest.packaging_missing",
            msg="packaging library not available, skipping version check",
        )
    except Exception as exc:
        issues.append(f"version compatibility check failed: {exc}")

    return issues


def _get_factory_version() -> str:
    """Get the current factory version from package metadata."""
    try:
        from importlib.metadata import version
        return version("remote-factory")
    except Exception:
        return "0.0.0"


def validate_capabilities(
    manifest: WorkflowManifest,
    node_types: set[str],
) -> list[str]:
    """Validate that workflow nodes comply with declared capabilities.

    If 'agent_only' is declared, FnNode is not allowed.

    Returns a list of issues (empty = valid).
    """
    issues: list[str] = []
    if "agent_only" in manifest.capabilities and "FnNode" in node_types:
        issues.append(
            f"workflow '{manifest.name}' declares 'agent_only' capability "
            "but contains FnNode(s)"
        )
    return issues


def manifest_from_meta(meta: dict[str, object], *, strict: bool = True) -> WorkflowManifest:
    """Create a WorkflowManifest from a legacy meta dict.

    When strict=False (for builtin/user/project sources), missing fields
    get defaults and a deprecation warning is emitted.
    """
    if strict:
        return WorkflowManifest.model_validate(meta)

    name = meta.get("name", "")
    description = meta.get("description", "")
    if not isinstance(name, str) or not name:
        raise ValueError("meta dict must have a non-empty 'name' string")
    if not isinstance(description, str):
        description = str(description) if description else ""

    has_manifest_fields = any(
        k in meta for k in ("schema_version", "capabilities", "min_factory_version", "author", "url")
    )

    if not has_manifest_fields:
        warnings.warn(
            f"Workflow '{name}' uses bare meta dict without manifest fields. "
            "Add schema_version, capabilities, etc. for full plugin support.",
            DeprecationWarning,
            stacklevel=2,
        )

    raw_sv = meta.get("schema_version", 1)
    raw_caps = meta.get("capabilities", [])

    return WorkflowManifest(
        name=str(name),
        description=str(description),
        schema_version=int(raw_sv) if isinstance(raw_sv, (int, float, str)) else 1,
        min_factory_version=meta.get("min_factory_version"),  # type: ignore[arg-type]
        capabilities=list(raw_caps) if isinstance(raw_caps, list) else [],
        author=str(meta.get("author", "")),
        url=str(meta.get("url", "")),
    )
