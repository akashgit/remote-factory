"""Ephemeral mode lifecycle management for outer loop evolution.

Each candidate workflow is registered as a temporary mode (evolve-gen{N}-{id[:8]})
so InnerLoop.step() can run it via `factory ceo --mode <name>`. Modes are stored
as JSON files in .factory/outer_loop/modes/ with content-addressable hashing.

Uses context manager protocol for guaranteed cleanup.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import structlog

from factory.workflow.primitives import Workflow

log = structlog.get_logger()


class EphemeralModeRegistry:
    """Register/cleanup/promote ephemeral workflow modes for evolution.

    Each mode is stored as a JSON file at .factory/outer_loop/modes/{mode_name}.json.
    A thin .py wrapper is also written to .factory/workflows/{mode_name}.py so the
    WorkflowRegistry can discover the mode when a sub-CEO runs --mode <name>.
    Naming: evolve-gen{N}-{individual_id[:8]} — never collides with main registry.

    When target_dir differs from project_dir (e.g. --project-dir targets a
    FeatureBench instance), wrappers and mode JSONs are also written to the
    target directory so the sub-CEO can resolve the ephemeral mode.
    """

    def __init__(self, project_dir: Path, target_dir: Path | None = None) -> None:
        self._project_dir = Path(project_dir)
        self._target_dir = Path(target_dir) if target_dir else None
        self._modes_dir = self._project_dir / ".factory" / "outer_loop" / "modes"
        self._workflows_dir = self._project_dir / ".factory" / "workflows"
        self._registered: dict[str, str] = {}

    @property
    def has_target(self) -> bool:
        return self._target_dir is not None and self._target_dir != self._project_dir

    def __enter__(self) -> EphemeralModeRegistry:
        self._modes_dir.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup_all()

    def _write_workflow_wrapper(self, mode_name: str, base_dir: Path | None = None) -> None:
        """Write a thin .py wrapper to .factory/workflows/ for WorkflowRegistry discovery."""
        workflows_dir = (base_dir or self._project_dir) / ".factory" / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        wrapper = (
            "import json\n"
            "from pathlib import Path\n"
            "from factory.workflow.primitives import Workflow\n"
            "\n"
            f"meta = {{'name': '{mode_name}', 'description': 'Ephemeral outer-loop candidate'}}\n"
            "\n"
            "def workflow():\n"
            f"    data_path = Path(__file__).parent.parent / 'outer_loop' / 'modes' / '{mode_name}.json'\n"
            "    data = json.loads(data_path.read_text())\n"
            "    data.pop('_content_hash', None)\n"
            "    return Workflow.from_dict(data)\n"
        )
        (workflows_dir / f"{mode_name}.py").write_text(wrapper)

    def _remove_workflow_wrapper(self, mode_name: str, base_dir: Path | None = None) -> None:
        """Remove the .py wrapper from .factory/workflows/."""
        workflows_dir = (base_dir or self._project_dir) / ".factory" / "workflows"
        wrapper = workflows_dir / f"{mode_name}.py"
        if wrapper.exists():
            wrapper.unlink()

    def register(
        self,
        individual_id: str,
        generation: int,
        workflow: Workflow,
    ) -> str:
        """Register a workflow as an ephemeral mode. Returns the mode name."""
        mode_name = f"evolve-gen{generation}-{individual_id[:8]}"
        self._modes_dir.mkdir(parents=True, exist_ok=True)

        wf_data = workflow.to_dict()
        wf_data["name"] = mode_name

        content = json.dumps(wf_data, indent=2, sort_keys=True)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        wf_data["_content_hash"] = content_hash

        mode_json = json.dumps(wf_data, indent=2, sort_keys=True)
        mode_path = self._modes_dir / f"{mode_name}.json"
        mode_path.write_text(mode_json)

        self._write_workflow_wrapper(mode_name)

        if self.has_target:
            assert self._target_dir is not None
            target_modes = self._target_dir / ".factory" / "outer_loop" / "modes"
            target_modes.mkdir(parents=True, exist_ok=True)
            (target_modes / f"{mode_name}.json").write_text(mode_json)
            self._write_workflow_wrapper(mode_name, base_dir=self._target_dir)
            log.debug("ephemeral_mode_mirrored_to_target", mode=mode_name, target=str(self._target_dir))

        self._registered[mode_name] = str(mode_path)
        log.info(
            "ephemeral_mode_registered",
            mode=mode_name,
            generation=generation,
            nodes=len(workflow.nodes),
            hash=content_hash,
        )
        return mode_name

    def load(self, mode_name: str) -> Workflow | None:
        """Load a registered ephemeral mode's workflow."""
        mode_path = self._modes_dir / f"{mode_name}.json"
        if not mode_path.exists():
            return None
        try:
            data = json.loads(mode_path.read_text())
            stored_hash = data.pop("_content_hash", None)
            if stored_hash:
                verify_data = dict(data)
                verify_content = json.dumps(verify_data, indent=2, sort_keys=True)
                actual_hash = hashlib.sha256(verify_content.encode()).hexdigest()[:16]
                if actual_hash != stored_hash:
                    log.warning(
                        "ephemeral_mode_hash_mismatch",
                        mode=mode_name,
                        expected=stored_hash,
                        actual=actual_hash,
                    )
            return Workflow.from_dict(data)
        except Exception:
            log.error("ephemeral_mode_load_failed", mode=mode_name, exc_info=True)
            return None

    def _remove_target_artifacts(self, mode_name: str) -> None:
        """Remove mirrored artifacts from the target directory."""
        if not self.has_target:
            return
        assert self._target_dir is not None
        target_mode = self._target_dir / ".factory" / "outer_loop" / "modes" / f"{mode_name}.json"
        if target_mode.exists():
            target_mode.unlink()
        self._remove_workflow_wrapper(mode_name, base_dir=self._target_dir)

    def cleanup_generation(self, survivors: set[str]) -> int:
        """Delete non-surviving mode files. Returns count of removed modes."""
        removed = 0
        if not self._modes_dir.exists():
            return 0

        for mode_file in self._modes_dir.glob("evolve-gen*.json"):
            mode_name = mode_file.stem
            if mode_name not in survivors:
                mode_file.unlink()
                self._remove_workflow_wrapper(mode_name)
                self._remove_target_artifacts(mode_name)
                self._registered.pop(mode_name, None)
                removed += 1

        if removed:
            log.info("ephemeral_modes_cleaned", removed=removed, survivors=len(survivors))
        return removed

    def cleanup_all(self, keep_best: str | None = None) -> int:
        """Delete all ephemeral mode files except optionally the best one."""
        removed = 0
        if not self._modes_dir.exists():
            return 0

        for mode_file in self._modes_dir.glob("evolve-gen*.json"):
            mode_name = mode_file.stem
            if mode_name == keep_best:
                continue
            mode_file.unlink()
            self._remove_workflow_wrapper(mode_name)
            self._remove_target_artifacts(mode_name)
            self._registered.pop(mode_name, None)
            removed += 1

        if removed:
            log.info("ephemeral_modes_cleanup_all", removed=removed, kept=keep_best)
        return removed

    def promote(self, mode_name: str, permanent_name: str) -> Path | None:
        """Copy an ephemeral mode to factory/workflow/contributed/ as a permanent mode."""
        mode_path = self._modes_dir / f"{mode_name}.json"
        if not mode_path.exists():
            log.error("promote_source_missing", mode=mode_name)
            return None

        contrib_dir = self._project_dir / "factory" / "workflow" / "contributed" / permanent_name
        contrib_dir.mkdir(parents=True, exist_ok=True)

        data = json.loads(mode_path.read_text())
        data.pop("_content_hash", None)
        data["name"] = permanent_name

        dest = contrib_dir / "workflow.json"
        dest.write_text(json.dumps(data, indent=2, sort_keys=True))

        log.info("ephemeral_mode_promoted", source=mode_name, dest=str(dest))
        return dest

    def prune_stale_modes(self, older_than_hours: int = 24) -> list[str]:
        """Remove ephemeral modes older than the given threshold.

        Returns list of pruned mode names.
        """
        if not self._modes_dir.exists():
            return []

        cutoff = time.time() - older_than_hours * 3600
        pruned: list[str] = []

        for mode_file in self._modes_dir.glob("evolve-gen*.json"):
            if mode_file.stat().st_mtime < cutoff:
                mode_name = mode_file.stem
                mode_file.unlink()
                self._remove_workflow_wrapper(mode_name)
                self._remove_target_artifacts(mode_name)
                self._registered.pop(mode_name, None)
                pruned.append(mode_name)

        if pruned:
            log.info("stale_modes_pruned", count=len(pruned), threshold_hours=older_than_hours)
        return pruned

    def list_modes(self) -> list[str]:
        """List all registered ephemeral mode names."""
        if not self._modes_dir.exists():
            return []
        return sorted(f.stem for f in self._modes_dir.glob("evolve-gen*.json"))

    @property
    def count(self) -> int:
        return len(self.list_modes())
