"""On-the-fly workflow skill generation with checksum-based caching."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import structlog

from factory.workflow.primitives import Workflow

log = structlog.get_logger()


def _sort_recursive(obj: object) -> object:
    """Recursively sort dicts by key and lists by value for deterministic serialization."""
    if isinstance(obj, dict):
        return {k: _sort_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        try:
            return sorted(_sort_recursive(item) for item in obj)
        except TypeError:
            return [_sort_recursive(item) for item in obj]
    return obj


def _compute_checksum(workflows: dict[str, Workflow]) -> str:
    """Deterministic checksum from workflow Pydantic models.

    Serialises all workflows via model_dump(mode='json'), sorts by name,
    then SHA-256 hashes the canonical JSON.  Returns the first 16 hex chars.
    """
    payload = {name: wf.model_dump(mode="json") for name, wf in sorted(workflows.items())}
    payload = _sort_recursive(payload)
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def ensure_skills(project_dir: Path) -> list[Path]:
    """Generate workflow skills into *project_dir*/skills/, using a local cache.

    Cache location: ``~/.factory/cache/skills/{checksum}/``.
    Only ``workflow-*`` subdirectories are copied — hand-written skills are
    never touched.  Returns an empty list on any I/O error (non-fatal).
    """
    try:
        return _ensure_skills_inner(project_dir)
    except OSError as exc:
        log.warning("skill_cache.error", error=str(exc))
        return []


def _ensure_skills_inner(project_dir: Path) -> list[Path]:
    from factory.workflow.definitions import register_all
    from factory.workflow.skill_export import export_all_skills

    workflows = register_all()
    checksum = _compute_checksum(workflows)

    cache_dir = Path.home() / ".factory" / "cache" / "skills" / checksum
    skills_target = project_dir / "skills"
    skills_target.mkdir(parents=True, exist_ok=True)

    workflow_dirs = sorted(cache_dir.glob("workflow-*")) if cache_dir.exists() else []

    if workflow_dirs:
        log.info("skill_cache.hit", checksum=checksum, cached_skills=len(workflow_dirs))
    else:
        log.info("skill_cache.miss", checksum=checksum)
        cache_dir.mkdir(parents=True, exist_ok=True)
        export_all_skills(cache_dir, workflows)
        workflow_dirs = sorted(cache_dir.glob("workflow-*"))

    generated: list[Path] = []
    for src in workflow_dirs:
        dst = skills_target / src.name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        skill_md = dst / "SKILL.md"
        if skill_md.exists():
            generated.append(skill_md)

    log.info("skill_cache.copied", count=len(generated), target=str(skills_target))
    return generated
