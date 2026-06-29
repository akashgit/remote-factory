"""Tests for expected-behavior soul/verification-points structure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.agents.runner import _EXPECTED_BEHAVIORS_DIR, load_expected_behavior


ALL_AGENTS = [
    "archivist", "builder", "ceo", "failure_analyst",
    "profiler", "qa", "refiner", "researcher", "strategist",
]

SLUG_MAP = {"failure_analyst": "failure-analyst"}


class TestDirectoryStructureExists:
    """Verify all 9 agents have both soul.md and verification-points.md."""

    def test_all_agents_have_soul(self) -> None:
        for agent in ALL_AGENTS:
            slug = SLUG_MAP.get(agent, agent)
            path = _EXPECTED_BEHAVIORS_DIR / slug / "soul.md"
            assert path.exists(), f"Missing soul.md for {agent}: {path}"

    def test_all_agents_have_verification_points(self) -> None:
        for agent in ALL_AGENTS:
            slug = SLUG_MAP.get(agent, agent)
            path = _EXPECTED_BEHAVIORS_DIR / slug / "verification-points.md"
            assert path.exists(), f"Missing verification-points.md for {agent}: {path}"

    def test_flat_files_still_exist(self) -> None:
        for agent in ALL_AGENTS:
            slug = SLUG_MAP.get(agent, agent)
            path = _EXPECTED_BEHAVIORS_DIR / f"{slug}.md"
            assert path.exists(), f"Flat file removed for {agent}: {path}"


class TestLoadExpectedBehavior:
    """Test the load_expected_behavior function."""

    def test_loads_directory_format(self) -> None:
        result = load_expected_behavior("builder")
        assert result["soul"] is not None
        assert result["verification_points"] is not None
        assert "Core Responsibility" in result["soul"]
        assert "Verification Points" in result["verification_points"]

    def test_loads_hyphenated_role(self) -> None:
        result = load_expected_behavior("failure_analyst")
        assert result["soul"] is not None
        assert result["verification_points"] is not None
        assert "Failure Analyst" in result["soul"]

    def test_all_agents_load_successfully(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert result["soul"] is not None, f"soul is None for {agent}"
            assert result["verification_points"] is not None, (
                f"verification_points is None for {agent}"
            )

    def test_falls_back_to_flat_file(self, tmp_path: Path) -> None:
        eb_dir = tmp_path / "expected-behaviors"
        eb_dir.mkdir()
        flat_file = eb_dir / "builder.md"
        flat_file.write_text("# Expected Behavior: Builder\nAll content here.")

        with patch("factory.agents.runner._EXPECTED_BEHAVIORS_DIR", eb_dir):
            result = load_expected_behavior("builder")
        assert result["soul"] == "# Expected Behavior: Builder\nAll content here."
        assert result["verification_points"] is None

    def test_prefers_directory_over_flat(self, tmp_path: Path) -> None:
        eb_dir = tmp_path / "expected-behaviors"
        eb_dir.mkdir()
        (eb_dir / "builder.md").write_text("flat content")
        builder_dir = eb_dir / "builder"
        builder_dir.mkdir()
        (builder_dir / "soul.md").write_text("soul content")
        (builder_dir / "verification-points.md").write_text("vp content")

        with patch("factory.agents.runner._EXPECTED_BEHAVIORS_DIR", eb_dir):
            result = load_expected_behavior("builder")
        assert result["soul"] == "soul content"
        assert result["verification_points"] == "vp content"

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        eb_dir = tmp_path / "expected-behaviors"
        eb_dir.mkdir()
        with patch("factory.agents.runner._EXPECTED_BEHAVIORS_DIR", eb_dir):
            result = load_expected_behavior("builder")
        assert result["soul"] is None
        assert result["verification_points"] is None


class TestSoulContent:
    """Verify soul.md files have required sections."""

    def test_soul_has_core_responsibility(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "Core Responsibility" in result["soul"], (
                f"soul.md for {agent} missing Core Responsibility"
            )

    def test_soul_has_position(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "Position in Factory Hierarchy" in result["soul"], (
                f"soul.md for {agent} missing Position in Factory Hierarchy"
            )

    def test_soul_has_decision_philosophy(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "Decision-Making Philosophy" in result["soul"], (
                f"soul.md for {agent} missing Decision-Making Philosophy"
            )


class TestVerificationPointsContent:
    """Verify verification-points.md files have required sections."""

    def test_vp_has_forbidden_actions(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "Forbidden Actions" in result["verification_points"], (
                f"verification-points.md for {agent} missing Forbidden Actions"
            )

    def test_vp_has_failure_modes(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "Failure Modes" in result["verification_points"], (
                f"verification-points.md for {agent} missing Failure Modes"
            )

    def test_vp_has_checkboxes(self) -> None:
        for agent in ALL_AGENTS:
            result = load_expected_behavior(agent)
            assert "- [ ]" in result["verification_points"], (
                f"verification-points.md for {agent} has no checklist items"
            )
