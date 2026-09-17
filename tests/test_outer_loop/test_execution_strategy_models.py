"""Tests for SwarmConfig.execution_strategy field validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory.outer_loop.models import SwarmConfig


class TestExecutionStrategyField:
    """SwarmConfig.execution_strategy field validation."""

    def test_default_is_executor(self) -> None:
        """Field defaults to 'executor' when omitted."""
        config = SwarmConfig(benchmark="test", budget=10)
        assert config.execution_strategy == "executor"

    def test_accepts_executor(self) -> None:
        config = SwarmConfig(benchmark="test", budget=10, execution_strategy="executor")
        assert config.execution_strategy == "executor"

    def test_accepts_ceo_skill(self) -> None:
        config = SwarmConfig(benchmark="test", budget=10, execution_strategy="ceo-skill")
        assert config.execution_strategy == "ceo-skill"

    def test_accepts_ceo_tool(self) -> None:
        config = SwarmConfig(benchmark="test", budget=10, execution_strategy="ceo-tool")
        assert config.execution_strategy == "ceo-tool"

    def test_rejects_invalid_value(self) -> None:
        with pytest.raises(ValidationError):
            SwarmConfig(benchmark="test", budget=10, execution_strategy="ceo-subprocess")

    def test_rejects_typo(self) -> None:
        with pytest.raises(ValidationError):
            SwarmConfig(benchmark="test", budget=10, execution_strategy="typo")

    def test_round_trip_all_values(self) -> None:
        """model_dump → model_validate round-trip preserves all strategy values."""
        for strategy in ("executor", "ceo-skill", "ceo-tool"):
            config = SwarmConfig(benchmark="test", budget=10, execution_strategy=strategy)
            dumped = config.model_dump(mode="json")
            restored = SwarmConfig.model_validate(dumped)
            assert restored.execution_strategy == strategy

    def test_old_config_without_field_defaults_to_executor(self) -> None:
        """Configs/checkpoints without execution_strategy get 'executor' default."""
        old_data = {"benchmark": "test", "budget": 10}
        config = SwarmConfig.model_validate(old_data)
        assert config.execution_strategy == "executor"

    def test_field_is_literal_not_str(self) -> None:
        """Verify field type annotation is Literal, not plain str."""
        import typing
        field_info = SwarmConfig.model_fields["execution_strategy"]
        annotation = field_info.annotation
        origin = getattr(annotation, "__origin__", None) or typing.get_origin(annotation)
        assert origin is typing.Literal
