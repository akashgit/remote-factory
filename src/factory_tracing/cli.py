"""CLI entry point for factory-tracing: verify and status commands."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import urllib.error

from dotenv import load_dotenv


REQUIRED_VARS = [
    "FACTORY_TRACING_ENABLED",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
]


def _run_status() -> int:
    load_dotenv()

    print("factory-tracing status")
    print("=" * 40)

    all_present = True
    for var in REQUIRED_VARS:
        value = os.environ.get(var, "")
        if value:
            masked = value[:4] + "***" if var.endswith("_KEY") else value
            print(f"  {var}: {masked}")
        else:
            print(f"  {var}: NOT SET")
            all_present = False

    langfuse_host = os.environ.get("LANGFUSE_HOST", "")
    if langfuse_host:
        health_url = f"{langfuse_host.rstrip('/')}/api/public/health"
        print(f"\nHealth check: {health_url}")
        try:
            req = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"  Status: {resp.status} OK")
        except urllib.error.URLError as exc:
            print(f"  Status: UNREACHABLE ({exc.reason})")
        except Exception as exc:
            print(f"  Status: ERROR ({exc})")
    else:
        print("\nHealth check: skipped (LANGFUSE_HOST not set)")

    if all_present:
        print("\nConfiguration: COMPLETE")
    else:
        print("\nConfiguration: INCOMPLETE — set missing variables")

    return 0


def _run_verify() -> int:
    from .verify import run_verification

    result = run_verification()
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="factory-tracing",
        description="Distributed tracing management for the factory",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("verify", help="Run 2-agent end-to-end verification against Langfuse (10 criteria)")
    subparsers.add_parser("status", help="Check configuration and Langfuse connectivity")

    args = parser.parse_args(argv)

    if args.command == "verify":
        return _run_verify()
    elif args.command == "status":
        return _run_status()
    else:
        parser.print_help()
        return 0


def _cli_entry() -> None:
    sys.exit(main())
