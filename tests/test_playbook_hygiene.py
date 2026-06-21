"""Guard tests: shipped code must stay clean of user-specific data.

If a test fails, it means someone (or ACE) wrote personal project
data into the source tree. Evolved playbooks belong in
~/.factory/playbooks/, not in the source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLAYBOOKS_DIR = REPO_ROOT / "factory" / "agents" / "playbooks"

FORBIDDEN_PLAYBOOK_PATTERNS = [
    "eric" + "a-agent",
    "cp-" + "agent",
    "backyard" + "-chronicle",
    "group-chat" + "-digest",
    "Pin" + "ergy",
    "mls" + "pin",
    "h3d.",
    "h3h.",
    "h3z.",
    "itpc" + "-gcp",
    "us-ea" + "st5",
    "remote-factory cycle",
    "Enter Your Agent ID",
    "cycle 35",
    "cycle 7",
    "#34-#36",
    "0.651",
    "96/96",
]

# Regex patterns that catch ANY user's absolute home paths, not just one developer's.
# /Users/<name>/ is macOS, /home/<name>/ is Linux. Both indicate a leaked local path.
# We allow "~/" and env-var-based paths ($HOME, $FACTORY_*) since those are portable.
ABSOLUTE_HOME_RE = re.compile(r"/(Users|home)/[a-zA-Z0-9._-]+/")

# Literal substrings that should never appear in shipped source.
FORBIDDEN_SOURCE_PATTERNS = [
    "cursor" + "-projects",
    "obsidian-vaults" + "/factory",
]

SOURCE_EXTENSIONS = {"*.py", "*.md"}

SOURCE_DIRS = [
    REPO_ROOT / "factory",
    REPO_ROOT / "docs",
]

# Files allowed to contain patterns (test file, changelog history, illustrative examples).
ALLOWLIST_ABSOLUTE = {
    Path("tests/test_playbook_hygiene.py"),
    Path("factory/study.py"),  # docstring example: "e.g. /home/dev/projects/my-app"
}
ALLOWLIST_SUBSTRINGS = {
    Path("tests/test_playbook_hygiene.py"),
    Path("CHANGELOG.md"),  # historical entries documenting past fixes
}


def _collect_source_files() -> list[Path]:
    files: list[Path] = []
    for src_dir in SOURCE_DIRS:
        for ext in SOURCE_EXTENSIONS:
            files.extend(src_dir.rglob(ext))
    # Also check top-level shipped files
    for ext in SOURCE_EXTENSIONS:
        files.extend(REPO_ROOT.glob(ext))
    return sorted(set(files))


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
            for pattern in FORBIDDEN_PLAYBOOK_PATTERNS:
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
        expected = {"archivist", "builder", "ceo", "qa", "strategist"}
        actual = {p.stem for p in PLAYBOOKS_DIR.glob("*.md")}
        assert expected == actual


class TestNoHardcodedPaths:
    """Source tree must not contain hardcoded user-specific paths.

    This catches ANY developer's paths — /Users/<anyone>/ or /home/<anyone>/ —
    plus known bad substrings. If your code needs a user-local path, use an
    env var (FACTORY_PROJECTS_DIR, FACTORY_VAULT_PATH, etc.) or ~/relative.
    """

    def test_no_absolute_home_paths(self):
        """No absolute /Users/<name>/ or /home/<name>/ paths in shipped code."""
        violations: list[str] = []
        for path in _collect_source_files():
            rel = path.relative_to(REPO_ROOT)
            if rel in ALLOWLIST_ABSOLUTE:
                continue
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if ABSOLUTE_HOME_RE.search(line):
                    violations.append(f"{rel}:{i}: {line.strip()[:100]}")
        assert not violations, (
            "Absolute home-directory paths found in source tree.\n"
            "Use env vars or ~/ instead:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_no_forbidden_substrings(self):
        """Known bad substrings must not appear in shipped source."""
        violations: list[str] = []
        for path in _collect_source_files():
            rel = path.relative_to(REPO_ROOT)
            if rel in ALLOWLIST_SUBSTRINGS:
                continue
            try:
                content = path.read_text()
            except UnicodeDecodeError:
                continue
            for pattern in FORBIDDEN_SOURCE_PATTERNS:
                if pattern in content:
                    violations.append(f"{rel}: contains '{pattern}'")
        assert not violations, (
            "Hardcoded user-specific paths found in source tree.\n"
            "Use env vars (FACTORY_PROJECTS_DIR, FACTORY_VAULT_PATH) instead:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
