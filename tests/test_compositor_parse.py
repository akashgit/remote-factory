"""Unit tests for the composition parser and validator."""

from __future__ import annotations

import pytest

from factory.workflow.compositor import (
    ParallelStep,
    SequentialStep,
    parse_mode_spec,
    validate_composition,
)


class TestSimpleSequential:
    def test_two_sequential(self) -> None:
        steps = parse_mode_spec("discover,improve")
        assert steps == [
            SequentialStep(mode="discover"),
            SequentialStep(mode="improve"),
        ]


class TestSimpleParallel:
    def test_two_parallel(self) -> None:
        steps = parse_mode_spec("a+b")
        assert steps == [ParallelStep(modes=["a", "b"])]


class TestMixed:
    def test_sequential_parallel_sequential(self) -> None:
        steps = parse_mode_spec("discover,a+b,improve")
        assert len(steps) == 3
        assert isinstance(steps[0], SequentialStep)
        assert steps[0].mode == "discover"
        assert isinstance(steps[1], ParallelStep)
        assert steps[1].modes == ["a", "b"]
        assert isinstance(steps[2], SequentialStep)
        assert steps[2].mode == "improve"


class TestSingleMode:
    def test_single(self) -> None:
        steps = parse_mode_spec("improve")
        assert steps == [SequentialStep(mode="improve")]


class TestWhitespaceHandling:
    def test_spaces_around_modes(self) -> None:
        steps = parse_mode_spec(" discover , improve ")
        assert steps == [
            SequentialStep(mode="discover"),
            SequentialStep(mode="improve"),
        ]

    def test_spaces_around_plus(self) -> None:
        steps = parse_mode_spec(" a + b ")
        assert steps == [ParallelStep(modes=["a", "b"])]


class TestEmptyStringError:
    def test_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            parse_mode_spec("")

    def test_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            parse_mode_spec("   ")


class TestTrailingComma:
    def test_trailing_comma_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty stage"):
            parse_mode_spec("a,")


class TestValidateUnknownMode:
    def test_unknown_mode_detected(self) -> None:
        steps = parse_mode_spec("custom1,custom2")
        errors = validate_composition(steps, registry_names={"custom1"})
        assert any("unknown mode 'custom2'" in e for e in errors)

    def test_all_known_passes(self) -> None:
        steps = parse_mode_spec("custom1,custom2")
        errors = validate_composition(steps, registry_names={"custom1", "custom2"})
        assert errors == []


class TestValidateBuiltinParallel:
    def test_builtin_in_parallel_errors(self) -> None:
        steps = [ParallelStep(modes=["discover", "custom"])]
        errors = validate_composition(steps)
        assert any("built-in mode 'discover'" in e for e in errors)

    def test_non_builtin_in_parallel_ok(self) -> None:
        steps = [ParallelStep(modes=["custom1", "custom2"])]
        errors = validate_composition(steps)
        assert errors == []


class TestValidateEmpty:
    def test_no_steps(self) -> None:
        errors = validate_composition([])
        assert any("at least one step" in e for e in errors)
