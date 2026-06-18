"""Shared test fixtures for factory-tracing tests."""

from __future__ import annotations

import os

import pytest

from factory_tracing.provider import reset_provider


@pytest.fixture(autouse=True)
def _reset_tracing():
    """Reset the tracer provider singleton between tests."""
    reset_provider()
    yield
    reset_provider()


@pytest.fixture(autouse=True)
def _tracing_env(monkeypatch):
    """Set minimal tracing env vars for tests."""
    monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
