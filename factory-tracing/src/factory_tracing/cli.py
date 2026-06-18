"""CLI entry point for factory-tracing verify/status commands."""

from __future__ import annotations

import argparse
import sys

from factory_tracing.config import TracingConfig


def cmd_status() -> None:
    """Show tracing configuration status."""
    config = TracingConfig.from_env()
    print(f"Tracing enabled: {config.enabled}")
    print(f"Langfuse host:   {config.langfuse_host}")
    print(f"OTLP endpoint:   {config.otlp_endpoint or '(not set)'}")
    print(f"Service name:    {config.service_name}")
    print(f"Public key:      {'***' + config.langfuse_public_key[-4:] if config.langfuse_public_key else '(not set)'}")


def cmd_verify() -> None:
    """Run verification against Langfuse."""
    from factory_tracing.verify import run_verification

    print("Running tracing verification...")
    print("This will spawn 2 test agents and check spans in Langfuse.\n")

    report = run_verification()

    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.name}: {check.detail}")

    print(f"\nResult: {report.passed}/{report.total} checks passed")
    if not report.all_passed:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="factory-tracing",
        description="OpenTelemetry tracing for Factory agent execution",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show tracing configuration")
    subparsers.add_parser("verify", help="Run verification against Langfuse")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "verify":
        cmd_verify()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
