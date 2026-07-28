"""Tests for factory.spec.apply_diff — SPEC Diff application from strategy."""

from __future__ import annotations

from pathlib import Path


from factory.spec.apply_diff import (
    apply_spec_diff,
    extract_spec_diff,
)
from factory.workflow.definitions import improve_workflow
from factory.workflow.primitives import FnNode


# ── extract_spec_diff ──────────────────────────────────────────


class TestExtractSpecDiff:
    def test_no_spec_diff_section(self) -> None:
        text = "## Strategy\n\nSome strategy content.\n\n## Hypotheses\n\nH1 stuff."
        assert extract_spec_diff(text) is None

    def test_empty_spec_diff(self) -> None:
        text = "## SPEC Diff\n\n## Hypotheses\n\nH1 stuff."
        diff = extract_spec_diff(text)
        assert diff is not None
        assert diff.added == []
        assert diff.modified == []
        assert diff.removed == []

    def test_added_modules(self) -> None:
        text = (
            "## SPEC Diff\n\n"
            "### ADDED Modules\n\n"
            "#### module `auth`\n"
            "- **Path:** `factory/auth.py`\n"
            "- **Role:** Authentication module\n"
            "- **Depends on:** `store`\n\n"
            "#### module `cache`\n"
            "- **Path:** `factory/cache.py`\n"
            "- **Role:** Caching layer\n"
            "- **Depends on:** `store`\n\n"
            "## Hypotheses\n\nH1 stuff."
        )
        diff = extract_spec_diff(text)
        assert diff is not None
        assert len(diff.added) == 2
        assert diff.added[0].name == "auth"
        assert "factory/auth.py" in diff.added[0].body
        assert diff.added[1].name == "cache"

    def test_modified_modules(self) -> None:
        text = (
            "## SPEC Diff\n\n"
            "### MODIFIED Modules\n\n"
            "#### module `store`\n"
            "- **Previously:** Handles experiment data\n"
            "- **Now:** Handles experiment data and caching\n"
            "- **Rationale:** Added cache support\n\n"
            "## Hypotheses\n"
        )
        diff = extract_spec_diff(text)
        assert diff is not None
        assert len(diff.modified) == 1
        assert diff.modified[0].name == "store"
        assert "caching" in diff.modified[0].body

    def test_removed_modules(self) -> None:
        text = (
            "## SPEC Diff\n\n"
            "### REMOVED Modules\n\n"
            "#### module `legacy`\n"
            "- **Previously:** Old compatibility layer\n"
            "- **Rationale:** No longer needed\n\n"
            "## Hypotheses\n"
        )
        diff = extract_spec_diff(text)
        assert diff is not None
        assert len(diff.removed) == 1
        assert diff.removed[0].name == "legacy"

    def test_all_categories(self) -> None:
        text = (
            "## SPEC Diff\n\n"
            "### ADDED Modules\n\n"
            "#### module `new_mod`\n"
            "- **Path:** `factory/new_mod.py`\n"
            "- **Role:** New module\n\n"
            "### MODIFIED Modules\n\n"
            "#### module `existing`\n"
            "- **Previously:** Old behavior\n"
            "- **Now:** New behavior\n"
            "- **Rationale:** Improvement\n\n"
            "### REMOVED Modules\n\n"
            "#### module `old_mod`\n"
            "- **Previously:** Legacy module\n"
            "- **Rationale:** Deprecated\n\n"
            "## Hypotheses\n"
        )
        diff = extract_spec_diff(text)
        assert diff is not None
        assert len(diff.added) == 1
        assert len(diff.modified) == 1
        assert len(diff.removed) == 1


# ── apply_spec_diff ────────────────────────────────────────────


class TestApplySpecDiff:
    def test_no_strategy_file(self, tmp_path: Path) -> None:
        result = apply_spec_diff(tmp_path)
        assert result is False

    def test_no_spec_diff_section_returns_false(self, tmp_path: Path) -> None:
        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## Strategy\n\nSome content.\n\n## Hypotheses\n\nH1."
        )
        result = apply_spec_diff(tmp_path)
        assert result is False

    def test_added_modules_appended(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("# SPEC\n\n### module `existing`\n\nExisting content.\n")

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## SPEC Diff\n\n"
            "### ADDED Modules\n\n"
            "#### module `auth`\n"
            "- **Path:** `factory/auth.py`\n"
            "- **Role:** Auth module\n\n"
            "## Hypotheses\n"
        )

        result = apply_spec_diff(tmp_path)
        assert result is True

        spec_text = spec_path.read_text()
        assert "### module `auth`" in spec_text
        assert "factory/auth.py" in spec_text
        assert "### module `existing`" in spec_text

    def test_modified_modules_replaced(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(
            "# SPEC\n\n"
            "### module `store`\n\nOld store content.\n\n"
            "### module `cli`\n\nCLI content.\n"
        )

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## SPEC Diff\n\n"
            "### MODIFIED Modules\n\n"
            "#### module `store`\n"
            "- **Previously:** Old store content\n"
            "- **Now:** New store with caching\n"
            "- **Rationale:** Performance\n\n"
            "## Hypotheses\n"
        )

        result = apply_spec_diff(tmp_path)
        assert result is True

        spec_text = spec_path.read_text()
        assert "New store with caching" in spec_text
        assert "\nOld store content.\n" not in spec_text
        assert "### module `cli`" in spec_text

    def test_removed_modules_deleted(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text(
            "# SPEC\n\n### module `legacy`\n\nLegacy stuff.\n\n### module `cli`\n\nCLI content.\n"
        )

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## SPEC Diff\n\n"
            "### REMOVED Modules\n\n"
            "#### module `legacy`\n"
            "- **Previously:** Legacy stuff\n"
            "- **Rationale:** Deprecated\n\n"
            "## Hypotheses\n"
        )

        result = apply_spec_diff(tmp_path)
        assert result is True

        spec_text = spec_path.read_text()
        assert "### module `legacy`" not in spec_text
        assert "### module `cli`" in spec_text

    def test_missing_spec_creates_new_file(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "SPEC.md"
        assert not spec_path.exists()

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## SPEC Diff\n\n"
            "### ADDED Modules\n\n"
            "#### module `new_mod`\n"
            "- **Path:** `factory/new_mod.py`\n"
            "- **Role:** Brand new module\n\n"
            "## Hypotheses\n"
        )

        result = apply_spec_diff(tmp_path)
        assert result is True
        assert spec_path.exists()

        spec_text = spec_path.read_text()
        assert "### module `new_mod`" in spec_text
        assert "Brand new module" in spec_text

    def test_custom_strategy_path(self, tmp_path: Path) -> None:
        custom_strategy = tmp_path / "my_strategy.md"
        custom_strategy.write_text(
            "## SPEC Diff\n\n"
            "### ADDED Modules\n\n"
            "#### module `custom`\n"
            "- **Path:** `custom.py`\n"
            "- **Role:** Custom module\n\n"
            "## End\n"
        )

        result = apply_spec_diff(tmp_path, strategy_path=custom_strategy)
        assert result is True

        spec_text = (tmp_path / "SPEC.md").read_text()
        assert "### module `custom`" in spec_text

    def test_modify_missing_module_appends(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "SPEC.md"
        spec_path.write_text("# SPEC\n\n### module `cli`\n\nCLI content.\n")

        strategy_dir = tmp_path / ".factory" / "strategy"
        strategy_dir.mkdir(parents=True)
        (strategy_dir / "current.md").write_text(
            "## SPEC Diff\n\n"
            "### MODIFIED Modules\n\n"
            "#### module `nonexistent`\n"
            "- **Previously:** N/A\n"
            "- **Now:** New behavior\n"
            "- **Rationale:** Module was missing from spec\n\n"
            "## Hypotheses\n"
        )

        result = apply_spec_diff(tmp_path)
        assert result is True

        spec_text = spec_path.read_text()
        assert "### module `nonexistent`" in spec_text
        assert "New behavior" in spec_text


# ── Improve workflow integration ───────────────────────────────


class TestImproveWorkflowIntegration:
    def test_apply_spec_diff_node_exists(self) -> None:
        wf = improve_workflow()
        assert "apply_spec_diff" in wf.nodes
        node = wf.nodes["apply_spec_diff"]
        assert isinstance(node, FnNode)
        assert "apply-diff" in node.command

    def test_apply_spec_diff_wired_after_gate_strategy(self) -> None:
        wf = improve_workflow()
        gate_strategy_targets = [e.target for e in wf.edges if e.source == "gate_strategy"]
        assert "apply_spec_diff" in gate_strategy_targets

    def test_apply_spec_diff_wired_before_begin(self) -> None:
        wf = improve_workflow()
        apply_targets = [e.target for e in wf.edges if e.source == "apply_spec_diff"]
        assert "begin" in apply_targets

    def test_improve_workflow_still_valid(self) -> None:
        wf = improve_workflow()
        issues = wf.validate_graph()
        assert issues == [], f"improve workflow has issues: {issues}"
