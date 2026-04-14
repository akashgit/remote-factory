"""Pydantic models for the ACE self-improvement system.

Playbooks are per-agent behavioral rules evolved from experiment data.
Each item has helpful/harmful counters that track empirical backing.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PlaybookItem(BaseModel):
    """A single playbook bullet with empirical counters."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str  # e.g. "strat-00001"
    content: str
    helpful: int = 0
    harmful: int = 0
    section: Literal["DO", "DON'T"] = "DO"

    @property
    def net_score(self) -> int:
        return self.helpful - self.harmful

    def to_line(self) -> str:
        return f"- [{self.id}] helpful={self.helpful} harmful={self.harmful} :: {self.content}"

    @classmethod
    def from_line(cls, line: str) -> PlaybookItem | None:
        """Parse a playbook bullet line. Returns None if unparseable."""
        m = re.match(
            r"^-\s+\[([^\]]+)\]\s+helpful=(\d+)\s+harmful=(\d+)\s+::\s+(.+)$",
            line.strip(),
        )
        if not m:
            return None
        return cls(
            id=m.group(1),
            content=m.group(4),
            helpful=int(m.group(2)),
            harmful=int(m.group(3)),
        )


class Playbook(BaseModel):
    """Per-agent behavioral playbook evolved from experiment data."""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: str
    updated: str = ""
    items: list[PlaybookItem] = []

    def to_markdown(self) -> str:
        """Serialize to markdown with frontmatter."""
        self.updated = date.today().isoformat()
        lines = [
            "---",
            f"role: {self.role}",
            f"updated: {self.updated}",
            f"item_count: {len(self.items)}",
            "---",
            "",
            f"## Behavioral Playbook — {self.role.title()}",
            "",
        ]

        do_items = [i for i in self.items if i.section == "DO"]
        dont_items = [i for i in self.items if i.section == "DON'T"]

        if do_items:
            lines.append("### DO")
            for item in sorted(do_items, key=lambda i: i.net_score, reverse=True):
                lines.append(item.to_line())
            lines.append("")

        if dont_items:
            lines.append("### DON'T")
            for item in sorted(dont_items, key=lambda i: i.net_score):
                lines.append(item.to_line())
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, text: str) -> Playbook:
        """Parse a playbook from markdown. Tolerant of missing sections."""
        role = "unknown"
        updated = ""
        items: list[PlaybookItem] = []

        # Parse frontmatter
        fm_match = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).splitlines():
                if line.startswith("role:"):
                    role = line.split(":", 1)[1].strip()
                elif line.startswith("updated:"):
                    updated = line.split(":", 1)[1].strip()

        # Track which section we're in
        current_section: Literal["DO", "DON'T"] = "DO"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "### DO":
                current_section = "DO"
                continue
            if stripped == "### DON'T":
                current_section = "DON'T"
                continue
            item = PlaybookItem.from_line(stripped)
            if item is not None:
                item.section = current_section
                items.append(item)

        return cls(role=role, updated=updated, items=items)

    @classmethod
    def empty(cls, role: str) -> Playbook:
        return cls(role=role, items=[])
