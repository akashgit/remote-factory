"""MemPalace write operations — called via 'factory mempalace write'."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from filelock import FileLock

from .helpers import (
    get_palace_path,
    get_project_name,
    kg_add_triple,
    kg_supersede,
    store_drawer,
)


def mp_write(project_path: Path) -> str:
    """Write project state to MemPalace: KG decisions + episodic storage + eval score + playbook rules.

    No-op if mempalace not installed.
    """
    try:
        from mempalace.knowledge_graph import KnowledgeGraph  # noqa: F401
    except ImportError:
        return ""

    pn = get_project_name(project_path)
    today = date.today().isoformat()

    with FileLock(project_path / ".factory/.mempalace.lock"):
        # --- Section 1: Record design decisions (from record_design_decisions) ---
        current = project_path / ".factory/strategy/current.md"
        if current.exists():
            text = current.read_text()
            lines = text.split("\n")

            for line in lines:
                if line.startswith("#### H"):
                    title = line.replace("#### ", "").strip()
                    try:
                        kg_add_triple(pn, "has_hypothesis", title, valid_from=today)
                    except Exception:
                        pass

            in_ap = False
            for line in lines:
                if "Anti-patterns" in line:
                    in_ap = True
                    continue
                if in_ap and line.startswith("- "):
                    try:
                        kg_add_triple(pn, "rejected_approach", line[2:].strip(), valid_from=today)
                    except Exception:
                        pass
                elif in_ap and line.startswith("#"):
                    break

            summary = ""
            for i, lne in enumerate(lines):
                if lne.startswith("## Strategy"):
                    if i + 1 < len(lines):
                        summary = lines[i + 1].strip()
                    break
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                kg_add_triple(pn, "design_session", ts + ": " + summary, valid_from=today)
            except Exception:
                pass

            headline = ""
            for line in lines:
                if line.startswith("## "):
                    headline = line[3:].strip()
                    break
            try:
                kg_supersede(pn, "current_strategy", "previous", headline, at=today)
            except Exception:
                pass

        # --- Section 2: Episodic storage (from archive_to_memory) ---
        wing = "project:" + pn
        try:
            palace = get_palace_path()

            # experiments room — combined current.md + build.md narrative
            current_file = project_path / ".factory/strategy/current.md"
            build_file = project_path / ".factory/archive/build.md"
            if current_file.exists() or build_file.exists():
                parts: list[str] = []
                if current_file.exists():
                    parts.append(current_file.read_text())
                if build_file.exists():
                    parts.append(build_file.read_text())
                store_drawer(
                    palace, wing=wing, room="experiments",
                    content="\n\n---\n\n".join(parts),
                    source_file=str(build_file) if build_file.exists() else str(current_file),
                )

            # failures room — gate reviews showing process failures + failing health checks
            reviews_dir = project_path / ".factory/reviews"
            if reviews_dir.exists():
                for vf in reviews_dir.glob("ceo-verdict-*.md"):
                    try:
                        vtext = vf.read_text()
                        if any(kw in vtext for kw in ("REDIRECT", "ABORT")):
                            store_drawer(
                                palace, wing=wing, room="failures",
                                content=vtext, source_file=str(vf),
                            )
                    except Exception:
                        pass
                hc = reviews_dir / "health-check.md"
                if hc.exists():
                    try:
                        hc_text = hc.read_text()
                        if "FAIL" in hc_text or "REVERT" in hc_text:
                            store_drawer(
                                palace, wing=wing, room="failures",
                                content=hc_text, source_file=str(hc),
                            )
                    except Exception:
                        pass

            # reviews room — each QA report as a separate drawer
            for qa_name in ("code-review.md", "adversarial-qa.md", "health-check.md"):
                qa_file = project_path / ".factory/reviews" / qa_name
                if qa_file.exists():
                    try:
                        store_drawer(
                            palace, wing=wing, room="reviews",
                            content=qa_file.read_text(), source_file=str(qa_file),
                        )
                    except Exception:
                        pass

            # research room — unchanged
            research_file = project_path / ".factory/strategy/research-combined.md"
            if research_file.exists():
                store_drawer(
                    palace, wing=wing, room="research",
                    content=research_file.read_text(), source_file=str(research_file),
                )

            # decisions room — final experiment verdict from verdict.json
            experiments_dir = project_path / ".factory/experiments"
            if experiments_dir.exists():
                exp_dirs = sorted(experiments_dir.iterdir(), reverse=True)
                for exp_dir in exp_dirs[:3]:
                    vj = exp_dir / "verdict.json"
                    if vj.exists():
                        try:
                            store_drawer(
                                palace, wing=wing, room="decisions",
                                content=vj.read_text(), source_file=str(vj),
                            )
                        except Exception:
                            pass
        except Exception:
            pass

        # --- Section 3: Update eval score in KG (from archive_to_memory) ---
        try:
            eval_file = project_path / ".factory/last_eval.json"
            if eval_file.exists():
                data = json.loads(eval_file.read_text())
                score = str(data.get("composite", 0.0))
                kg_supersede(pn, "eval_score", "previous", score, at=today)
        except Exception:
            pass

        # --- Section 4: Store playbook rules as KG triples ---
        try:
            playbooks = Path(os.path.expanduser("~/.factory/playbooks"))
            if playbooks.exists():
                for rf in playbooks.glob("*.md"):
                    role = rf.stem
                    rule_lines = [ln for ln in rf.read_text().split("\n") if ln.startswith("- [")][:5]
                    for rl in rule_lines:
                        parts = rl.split(" :: ", 1)
                        rule = parts[1] if len(parts) > 1 else rl
                        try:
                            kg_supersede("playbook:" + role, "has_rule", "previous", rule, at=today)
                        except Exception:
                            pass
        except Exception:
            pass

    return "MemPalace archive complete for " + pn
