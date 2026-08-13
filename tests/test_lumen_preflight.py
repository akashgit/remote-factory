"""Tests for Lumen preflight — run directory setup and config merging."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.lumen.preflight import (
    derive_training_params,
    make_run_tag,
)

RUN_DIR_RE = re.compile(r"^run-\d{8}-\d{6}$")


class TestMakeRunTag:
    def test_format(self) -> None:
        tag = make_run_tag()
        assert re.match(r"^\d{8}-\d{6}$", tag)

    def test_unique(self) -> None:
        tags = {make_run_tag() for _ in range(5)}
        assert len(tags) >= 1


class TestDeriveTrainingParams:
    def test_overrides_gpu_count(self) -> None:
        gpu_info = {"gpu_count": 4, "gpu_type": "A100", "gpu_memory_mb": 81920}
        defaults = {"num_gpus": 8, "rollout_tp": 4}
        result = derive_training_params(gpu_info, defaults)
        assert result["num_gpus"] == 4

    def test_caps_rollout_tp(self) -> None:
        gpu_info = {"gpu_count": 2, "gpu_type": "A100", "gpu_memory_mb": 81920}
        defaults = {"num_gpus": 8, "rollout_tp": 4}
        result = derive_training_params(gpu_info, defaults)
        assert result["rollout_tp"] <= 2

    def test_no_gpu_keeps_defaults(self) -> None:
        gpu_info = {"gpu_count": 0, "gpu_type": "none", "gpu_memory_mb": 0}
        defaults = {"num_gpus": 8, "rollout_tp": 4}
        result = derive_training_params(gpu_info, defaults)
        assert result["num_gpus"] == 8
        assert result["rollout_tp"] == 4

    def test_matching_gpu_count_unchanged(self) -> None:
        gpu_info = {"gpu_count": 8, "gpu_type": "A100", "gpu_memory_mb": 81920}
        defaults = {"num_gpus": 8, "rollout_tp": 4}
        result = derive_training_params(gpu_info, defaults)
        assert result["num_gpus"] == 8
        assert result["rollout_tp"] == 4


class TestPreflightCli:
    def _find_run_dir(self, lumen_dir: Path) -> Path:
        """Find the single run-* directory under lumen_dir."""
        runs = [d for d in lumen_dir.iterdir() if d.is_dir() and RUN_DIR_RE.match(d.name)]
        assert len(runs) == 1, f"Expected 1 run dir, found {len(runs)}: {runs}"
        return runs[0]

    def test_mock_creates_run_dir(self, tmp_path: Path) -> None:
        task_dir = tmp_path / "benchmarks" / "einsteinarena" / "circle-packing"
        task_dir.mkdir(parents=True)
        (task_dir / "config.json").write_text(json.dumps({
            "num_gpus": 8, "rollout_tp": 4, "model_path": "Qwen/Qwen3-8B",
        }))
        (task_dir / "instruction.md").write_text("# Test\n")
        (task_dir / "verifier.py").write_text("def evaluate(d): return 1.0\n")

        from factory.lumen.preflight import main
        import sys

        with patch.object(sys, "argv", [
            "preflight",
            "--project-path", str(tmp_path),
            "--task-dir", str(task_dir),
            "--mock",
        ]):
            main()

        lumen_dir = tmp_path / ".factory" / "lumen"
        run_dir = self._find_run_dir(lumen_dir)
        assert (lumen_dir / "current_run").is_symlink()

        config = json.loads((run_dir / "config.json").read_text())
        assert config["task_name"] == "circle-packing"
        assert config["mock"] is True

        state = json.loads((run_dir / "state.json").read_text())
        assert state["iteration"] == 0

    def test_reads_task_from_launch_config(self, tmp_path: Path) -> None:
        """Preflight reads task_name from .factory/lumen/config.json when --task-dir omitted."""
        task_dir = tmp_path / "benchmarks" / "einsteinarena" / "my-task"
        task_dir.mkdir(parents=True)
        (task_dir / "config.json").write_text(json.dumps({
            "num_gpus": 4, "rollout_tp": 2, "model_path": "Qwen/Qwen3-8B",
        }))

        lumen_dir = tmp_path / ".factory" / "lumen"
        lumen_dir.mkdir(parents=True)
        (lumen_dir / "config.json").write_text(json.dumps({
            "task_name": "my-task",
            "mock": True,
        }))

        from factory.lumen.preflight import main
        import sys

        with patch.object(sys, "argv", [
            "preflight",
            "--project-path", str(tmp_path),
        ]):
            main()

        run_dir = self._find_run_dir(lumen_dir)
        config = json.loads((run_dir / "config.json").read_text())
        assert config["task_name"] == "my-task"
        assert config["mock"] is True

    def test_no_task_dir_no_config_exits(self, tmp_path: Path) -> None:
        """Preflight exits with error when neither --task-dir nor config.json exists."""
        from factory.lumen.preflight import main
        import sys

        with patch.object(sys, "argv", [
            "preflight",
            "--project-path", str(tmp_path),
        ]):
            with pytest.raises(SystemExit, match="1"):
                main()

    def test_current_symlink_updates(self, tmp_path: Path) -> None:
        """Running preflight twice creates two run dirs; current points to the latest."""
        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "config.json").write_text(json.dumps({"num_gpus": 4, "rollout_tp": 2}))

        from factory.lumen.preflight import main
        import sys
        import time

        with patch.object(sys, "argv", [
            "preflight", "--project-path", str(tmp_path),
            "--task-dir", str(task_dir), "--mock",
        ]):
            main()

        lumen_dir = tmp_path / ".factory" / "lumen"
        first_run = self._find_run_dir(lumen_dir)

        time.sleep(1.1)

        with patch.object(sys, "argv", [
            "preflight", "--project-path", str(tmp_path),
            "--task-dir", str(task_dir), "--mock",
        ]):
            main()

        runs = sorted(
            d for d in lumen_dir.iterdir() if d.is_dir() and RUN_DIR_RE.match(d.name)
        )
        assert len(runs) == 2
        assert runs[0] == first_run
        assert (lumen_dir / "current_run").resolve().name == runs[1].name
