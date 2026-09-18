"""Tests for factory task CLI commands: instances, setup, verify."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterator

import pytest

from factory.cli.task import (
    _cmd_task_instances,
    _cmd_task_setup,
    _cmd_task_verify,
    _find_instance,
    _load_task_from_ref,
)
from factory.task import Task, TaskDefinition, TaskInstance, VerifyResult


# ── Concrete Task subclass for testing ───────────────────────────


class _StubTask(Task):
    """Minimal Task subclass with two instances for testing CLI commands."""

    def __init__(self) -> None:
        super().__init__(TaskDefinition(name="stub"))

    def instances(self) -> Iterator[TaskInstance]:
        yield TaskInstance(id="alpha", path=Path("/tmp/alpha"), metadata={"k": "v1"})
        yield TaskInstance(id="beta", metadata={"k": "v2"})

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        (workspace / f"setup-{instance.id}").touch()

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        return VerifyResult(passed=True, score=0.8, details={"inst": instance.id})


# ── _load_task_from_ref tests ────────────────────────────────────


class TestLoadTaskFromRef:
    def test_valid_ref(self) -> None:
        ref = f"{_StubTask.__module__}:{_StubTask.__name__}"
        task = _load_task_from_ref(ref)
        assert isinstance(task, Task)
        assert task.name == "stub"

    def test_invalid_format_no_colon(self) -> None:
        with pytest.raises(ValueError, match="Expected"):
            _load_task_from_ref("no_colon_here")

    def test_module_not_found(self) -> None:
        with pytest.raises(ImportError):
            _load_task_from_ref("nonexistent.module:Cls")

    def test_class_not_found(self) -> None:
        with pytest.raises(ImportError, match="no attribute"):
            _load_task_from_ref(f"{_StubTask.__module__}:NonexistentClass")

    def test_not_a_task_subclass(self) -> None:
        with pytest.raises(TypeError, match="not a Task subclass"):
            _load_task_from_ref(f"{_StubTask.__module__}:Path")


# ── _find_instance tests ────────────────────────────────────────


class TestFindInstance:
    def test_found(self) -> None:
        task = _StubTask()
        inst = _find_instance(task, "alpha")
        assert inst.id == "alpha"

    def test_not_found(self) -> None:
        task = _StubTask()
        with pytest.raises(ValueError, match="not found"):
            _find_instance(task, "missing")


# ── _cmd_task_instances ──────────────────────────────────────────


class TestCmdTaskInstances:
    def test_prints_jsonl(self, capsys: pytest.CaptureFixture[str]) -> None:
        ref = f"{_StubTask.__module__}:{_StubTask.__name__}"
        args = argparse.Namespace(task_ref=ref)
        rc = _cmd_task_instances(args)
        assert rc == 0

        lines = capsys.readouterr().out.strip().split("\n")
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["id"] == "alpha"
        assert first["path"] == "/tmp/alpha"
        assert first["metadata"] == {"k": "v1"}

        second = json.loads(lines[1])
        assert second["id"] == "beta"
        assert second["path"] is None
        assert second["metadata"] == {"k": "v2"}


# ── _cmd_task_setup ──────────────────────────────────────────────


class TestCmdTaskSetup:
    def test_calls_setup(self, tmp_path: Path) -> None:
        ref = f"{_StubTask.__module__}:{_StubTask.__name__}"
        args = argparse.Namespace(
            task_ref=ref,
            instance_id="alpha",
            workspace=str(tmp_path),
        )
        rc = _cmd_task_setup(args)
        assert rc == 0
        assert (tmp_path / "setup-alpha").exists()

    def test_instance_not_found(self, tmp_path: Path) -> None:
        ref = f"{_StubTask.__module__}:{_StubTask.__name__}"
        args = argparse.Namespace(
            task_ref=ref,
            instance_id="missing",
            workspace=str(tmp_path),
        )
        with pytest.raises(ValueError, match="not found"):
            _cmd_task_setup(args)


# ── _cmd_task_verify ─────────────────────────────────────────────


class TestCmdTaskVerify:
    def test_prints_json(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        ref = f"{_StubTask.__module__}:{_StubTask.__name__}"
        args = argparse.Namespace(
            task_ref=ref,
            instance_id="alpha",
            workspace=str(tmp_path),
        )
        rc = _cmd_task_verify(args)
        assert rc == 0

        out = json.loads(capsys.readouterr().out.strip())
        assert out["score"] == 0.8
        assert out["passed"] is True
        assert out["details"] == {"inst": "alpha"}
