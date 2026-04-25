"""Guard test: shipped playbooks must stay clean of user-specific data.

If this test fails, it means someone (or ACE) wrote personal project
data into the factory default playbooks. Evolved playbooks belong in
~/.factory/playbooks/, not in the source tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PLAYBOOKS_DIR = Path(__file__).parent.parent / "factory" / "agents" / "playbooks"

FORBIDDEN_PATTERNS = [
    "example-project",
    "example-agent",
    "example-project-2",
    "example-digest",
    "ExampleCo",
    "example-data",
    "h3d.",
    "h3h.",
    "h3z.",
    "example-cloud",
    "us-region",
    "remote-factory cycle",
    "Enter Your Agent ID",
    "cycle 35",
    "cycle 7",
    "#34-#36",
    "0.651",
    "96/96",
]


@pytest.fixture
def playbook_files():
    return sorted(PLAYBOOKS_DIR.glob("*.md"))


class TestPlaybookHygiene:
    def test_playbooks_dir_exists(self):
        assert PLAYBOOKS_DIR.is_dir()

    def test_no_personal_data(self, playbook_files):
        """Shipped playbooks must not contain user-specific project references."""
        for path in playbook_files:
            content = path.read_text()
            for pattern in FORBIDDEN_PATTERNS:
                assert pattern not in content, (
                    f"{path.name} contains personal data: '{pattern}'. "
                    f"Evolved playbooks belong in ~/.factory/playbooks/, "
                    f"not in the source tree."
                )

    def test_counters_are_zero(self, playbook_files):
        """Shipped defaults should have zeroed counters — they're starting points."""
        from factory.ace.models import Playbook

        for path in playbook_files:
            playbook = Playbook.from_markdown(path.read_text())
            for item in playbook.items:
                assert item.helpful == 0, (
                    f"{path.name} [{item.id}] has helpful={item.helpful}. "
                    f"Shipped defaults must have zeroed counters."
                )
                assert item.harmful == 0, (
                    f"{path.name} [{item.id}] has harmful={item.harmful}. "
                    f"Shipped defaults must have zeroed counters."
                )

    def test_item_count_matches(self, playbook_files):
        """Frontmatter item_count must match actual number of items."""
        from factory.ace.models import Playbook

        for path in playbook_files:
            playbook = Playbook.from_markdown(path.read_text())
            content = path.read_text()
            for line in content.splitlines():
                if line.startswith("item_count:"):
                    declared = int(line.split(":")[1].strip())
                    assert declared == len(playbook.items), (
                        f"{path.name}: item_count says {declared} "
                        f"but has {len(playbook.items)} items"
                    )
                    break

    def test_expected_roles_present(self):
        """All six agent roles should have a shipped default playbook."""
        expected = {"archivist", "builder", "ceo", "evaluator", "reviewer", "strategist"}
        actual = {p.stem for p in PLAYBOOKS_DIR.glob("*.md")}
        assert expected == actual
