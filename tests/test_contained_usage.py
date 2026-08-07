"""Which runtimes this machine actually uses — the record that keeps `ls` off an unwanted cluster.

Somebody who answered "local" at setup should not be told their cluster is down, and asking an
unreachable one costs a multi-second timeout before that wrong answer arrives. The record is the
only thing standing between those two behaviours, so it has to be both durable and *never fatal*:
a machine whose home directory is read-only still has to be able to run a container.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained.usage import record_target, used_targets, uses


@pytest.fixture(autouse=True)
def contained_root(tmp_path: Path):
    root = tmp_path / "contained-home"
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(root)}, clear=False):
        yield root


def test_a_machine_with_no_record_uses_nothing() -> None:
    assert used_targets() == []
    assert not uses("k8s")


def test_recording_a_target_makes_it_used(contained_root: Path) -> None:
    record_target("k8s")
    assert uses("k8s") and not uses("local")


def test_recording_is_idempotent_and_does_not_rewrite_the_file(contained_root: Path) -> None:
    record_target("local")
    path = contained_root / "targets.json"
    before = path.stat().st_mtime_ns
    record_target("local")
    assert path.stat().st_mtime_ns == before


def test_both_targets_can_be_recorded(contained_root: Path) -> None:
    record_target("local")
    record_target("k8s")
    assert set(used_targets()) == {"local", "k8s"}


def test_an_unknown_target_is_ignored_rather_than_recorded(contained_root: Path) -> None:
    """The record drives which backends `ls` consults; a name nothing knows how to list would be
    read back and silently dropped anyway."""
    record_target("mainframe")
    assert not (contained_root / "targets.json").exists()


def test_an_unwritable_home_does_not_stop_the_run(contained_root: Path) -> None:
    """The only cost of failing here is that `ls` asks about one target more than it needs to."""
    with patch("pathlib.Path.write_text", side_effect=OSError("read-only file system")):
        record_target("local")
    assert used_targets() == []


def test_a_corrupt_record_reads_as_empty_rather_than_raising(contained_root: Path) -> None:
    contained_root.mkdir(parents=True)
    (contained_root / "targets.json").write_text("{not json")
    assert used_targets() == []


def test_a_record_that_is_not_a_list_reads_as_empty(contained_root: Path) -> None:
    contained_root.mkdir(parents=True)
    (contained_root / "targets.json").write_text(json.dumps({"local": True}))
    assert used_targets() == []


def test_unknown_names_in_the_record_are_filtered_out(contained_root: Path) -> None:
    """A record written by a newer version must not make this one try to list a target it has no
    backend for."""
    contained_root.mkdir(parents=True)
    (contained_root / "targets.json").write_text(json.dumps(["local", "mainframe"]))
    assert used_targets() == ["local"]
