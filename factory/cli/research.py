"""CLI research commands."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import structlog
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

log = structlog.get_logger()

from factory.cli._helpers import _run

def cmd_leakage_check(args: argparse.Namespace) -> int:
    """Check text for ground truth leakage against fixed surface fingerprints."""
    from factory.research.leakage import fingerprint_fixed_surfaces, scan_for_leakage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    if not config.fixed_surfaces:
        print("SKIP: no fixed_surfaces configured in factory.md")
        return 0

    fingerprints = fingerprint_fixed_surfaces(project_path, config.fixed_surfaces)
    if not fingerprints:
        print("SKIP: no fixed surface files found to fingerprint")
        return 0

    text = args.text
    if args.text_file:
        text_path = Path(args.text_file)
        if not text_path.is_file():
            print(f"ERROR: text file not found: {args.text_file}")
            return 1
        text = text_path.read_text()
    elif args.text is None:
        import sys
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("ERROR: provide --text, --text-file, or pipe to stdin")
            return 1

    report = scan_for_leakage(text, fingerprints, args.sensitivity)

    output = {
        "flagged": report.flagged,
        "risk_level": report.risk_level,
        "findings": [
            {
                "source_file": f.source_file,
                "leaked_token": f.leaked_token,
                "context": f.context,
                "leak_type": f.leak_type,
            }
            for f in report.findings
        ],
    }
    print(json.dumps(output, indent=2))
    return 1 if report.risk_level in ("medium", "high") else 0


def cmd_validate_research(args: argparse.Namespace) -> int:
    """Validate research mode configuration for ground truth isolation."""
    from factory.research.leakage import validate_research_config
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    config = _run(store.read_config())

    errors = validate_research_config(config, project_path)

    if not errors:
        print("VALID: research config passes all ground truth isolation checks")
        return 0

    for error in errors:
        print(f"ERROR: {error}")
    return 1


def cmd_research(args: argparse.Namespace) -> int:
    """Print citation index table and coverage summary."""
    from factory.research_index import build_citation_index, citation_coverage
    from factory.store import ExperimentStore

    project_path = Path(args.path).resolve()
    store = ExperimentStore(project_path)
    records = _run(store.load_history())

    if not records:
        print("No experiments recorded.")
        return 0

    index = build_citation_index(project_path)
    coverage = citation_coverage(project_path)

    # Print table
    header = f"{'ID':>4}  {'Hypothesis':<52}  Citations"
    print(header)
    print("-" * len(header))
    for r in records:
        hyp = r.hypothesis[:50]
        cites = index.get(r.id, [])
        cite_str = ", ".join(cites) if cites else "-"
        print(f"{r.id:>4}  {hyp:<52}  {cite_str}")

    # Summary
    cited_count = sum(1 for r in records if r.research_citations)
    print()
    print(f"{len(records)} experiments, {cited_count} cited, coverage {coverage:.0%}")
    return 0


def cmd_backfill_citations(args: argparse.Namespace) -> int:
    """Backfill citations from experiment text into .factory/citations.json."""
    from factory.research_index import backfill_citations

    project_path = Path(args.path).resolve()
    index = backfill_citations(project_path)
    print(f"Backfilled citations for {len(index)} experiments")
    for exp_id, cites in sorted(index.items(), key=lambda x: int(x[0])):
        print(f"  #{exp_id}: {', '.join(cites[:5])}")
    return 0

