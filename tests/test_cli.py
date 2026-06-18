"""Tests for factory_tracing.cli — status command with various env var configs."""
from __future__ import annotations

from unittest.mock import patch

from factory_tracing.cli import main, _run_status


@patch("factory_tracing.cli.load_dotenv")
def test_status_reports_missing_vars(_mock_dotenv, monkeypatch, capsys):
    for var in ("FACTORY_TRACING_ENABLED", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    exit_code = main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "NOT SET" in captured.out
    assert "INCOMPLETE" in captured.out


@patch("factory_tracing.cli.load_dotenv")
def test_status_reports_present_vars(_mock_dotenv, monkeypatch, capsys):
    monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-key-12345")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-key-12345")

    exit_code = main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "FACTORY_TRACING_ENABLED: 1" in captured.out
    assert "LANGFUSE_HOST: http://localhost:3000" in captured.out
    assert "pk-t***" in captured.out
    assert "sk-t***" in captured.out


@patch("factory_tracing.cli.load_dotenv")
def test_status_masks_secret_keys(_mock_dotenv, monkeypatch, capsys):
    monkeypatch.setenv("FACTORY_TRACING_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-full-public-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-full-secret-key")

    main(["status"])
    captured = capsys.readouterr()

    assert "pk-lf-full-public-key" not in captured.out
    assert "sk-lf-full-secret-key" not in captured.out


@patch("factory_tracing.cli.load_dotenv")
def test_status_skips_health_check_when_no_host(_mock_dotenv, monkeypatch, capsys):
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("FACTORY_TRACING_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    main(["status"])
    captured = capsys.readouterr()

    assert "skipped" in captured.out.lower()


def test_main_no_args_returns_zero(capsys):
    exit_code = main([])
    assert exit_code == 0


def test_main_verify_subcommand_exists(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert "verify" in captured.out
    assert "status" in captured.out
