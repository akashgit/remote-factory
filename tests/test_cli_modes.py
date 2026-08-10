"""Tests for --modes and --resume-from CLI argument parsing."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from factory.cli import build_parser


@pytest.fixture
def parser():
    return build_parser()


class TestModesFlag:
    def test_modes_flag_accepted_on_run(self, parser) -> None:
        args = parser.parse_args(["run", "/tmp/proj", "--modes", "discover,improve"])
        assert args.modes == "discover,improve"

    def test_modes_flag_accepted_on_ceo(self, parser) -> None:
        args = parser.parse_args(["ceo", "/tmp/proj", "--modes", "discover,a+b,improve"])
        assert args.modes == "discover,a+b,improve"

    def test_resume_from_flag_accepted(self, parser) -> None:
        args = parser.parse_args(["run", "/tmp/proj", "--modes", "discover,improve", "--resume-from", "improve"])
        assert args.resume_from == "improve"
        assert args.modes == "discover,improve"

    def test_modes_defaults_to_none(self, parser) -> None:
        args = parser.parse_args(["run", "/tmp/proj"])
        assert args.modes is None

    def test_resume_from_defaults_to_none(self, parser) -> None:
        args = parser.parse_args(["run", "/tmp/proj"])
        assert args.resume_from is None


class TestModesMutualExclusivity:
    def test_modes_with_explicit_mode_errors(self, tmp_path) -> None:
        from factory.cli.run import cmd_run
        from pathlib import Path

        parser = build_parser()
        args = parser.parse_args(["run", str(tmp_path), "--modes", "discover,improve", "--mode", "build"])
        with patch("factory.user_config.load_config"), \
             patch("factory.cli.run._resolve_input", return_value=(tmp_path, None)):
            code = cmd_run(args)
        assert code == 1

    def test_modes_with_loop_errors(self, tmp_path) -> None:
        from factory.cli.run import cmd_run

        parser = build_parser()
        args = parser.parse_args(["run", str(tmp_path), "--modes", "discover,improve", "--loop"])
        with patch("factory.user_config.load_config"), \
             patch("factory.cli.run._resolve_input", return_value=(tmp_path, None)):
            code = cmd_run(args)
        assert code == 1


class TestModesInvalidSpec:
    def test_empty_modes_spec_errors(self, tmp_path) -> None:
        from factory.cli.run import cmd_run

        parser = build_parser()
        args = parser.parse_args(["run", str(tmp_path), "--modes", ""])
        with patch("factory.user_config.load_config"), \
             patch("factory.cli.run._resolve_input", return_value=(tmp_path, None)):
            code = cmd_run(args)
        assert code == 1
