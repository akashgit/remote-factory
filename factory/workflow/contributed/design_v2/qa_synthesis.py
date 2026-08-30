"""Synthesize adversarial QA reports into a single merged report.

Called by the synthesize_qa FnNode in the design-v2 workflow.
Usage: python3 -m factory.workflow.contributed.design_v2.qa_synthesis <project_path>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    project = sys.argv[1]
    reports: list[tuple[str, str]] = []
    for p in sorted(Path(f"{project}/.factory/reviews").glob("adversarial-*-latest.md")):
        slug = p.name.replace("-latest.md", "").replace("adversarial-", "")
        reports.append((slug, p.read_text()))

    findings: dict[str, list[str]] = {}
    for tester_slug, text in reports:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                key = re.sub(r"\s+", " ", stripped[2:].strip().lower()[:200])
                findings.setdefault(key, []).append(tester_slug)

    high = [(k, v) for k, v in findings.items() if len(v) >= 2]
    medium = [(k, v) for k, v in findings.items() if len(v) == 1]

    out: list[str] = ["# Synthesized QA Report\n"]

    hc = Path(f"{project}/.factory/reviews/health-check.md")
    cr = Path(f"{project}/.factory/reviews/code-review.md")
    out.append("## Health Check\n")
    out.append(hc.read_text() if hc.exists() else "(not available)")
    out.append("\n## Code Review\n")
    out.append(cr.read_text() if cr.exists() else "(not available)")

    out.append("\n## High-Confidence Adversarial Findings (caught by 2+ testers)\n")
    for k, v in high:
        out.append(f"- {k} (testers: {v})")
    if not high:
        out.append("- (none)")

    out.append("\n## Medium-Confidence Adversarial Findings (single tester)\n")
    for k, v in medium:
        out.append(f"- {k} (tester: {v[0]})")
    if not medium:
        out.append("- (none)")

    out.append("\n## Raw Adversarial Reports\n")
    for slug, text in reports:
        out.append(f"### Tester: {slug}\n{text}\n")

    Path(f"{project}/.factory/reviews/qa-synthesized.md").write_text("\n".join(out))
    print(
        f"Synthesized {len(high)} high + {len(medium)} medium "
        f"findings from {len(reports)} adversarial reports"
    )


if __name__ == "__main__":
    main()
