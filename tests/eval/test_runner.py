"""Tests for factory.eval.runner — mandatory dimensions + project eval execution."""

import sys

from factory.eval.runner import run_eval


class TestRunEval:
    async def test_always_has_mandatory_dimensions(self, tmp_path):
        """Even with no project eval, all 12 mandatory dimensions are present."""
        # No eval/score.py — just mandatory dimensions
        result = await run_eval("true", tmp_path, threshold=0.0)
        names = {r.name for r in result.results}
        # 6 hygiene + 6 growth = 12 mandatory
        assert "tests" in names
        assert "lint" in names
        assert "type_check" in names
        assert "coverage" in names
        assert "guard_patterns" in names
        assert "config_parser" in names
        assert "capability_surface" in names
        assert "experiment_diversity" in names
        assert "observability" in names
        assert "research_grounding" in names
        assert "factory_effectiveness" in names
        assert "spec_compliance" in names
        assert len(result.results) >= 12

    async def test_project_additions_merged(self, tmp_path):
        """Project eval/score.py can add extra dimensions beyond the 11."""
        script = tmp_path / "score.py"
        script.write_text(
            'import json, sys\n'
            'json.dump({"results": ['
            '{"name": "ui_renders", "score": 0.9, "weight": 0.5, "passed": True, "details": "ok"},'
            '{"name": "api_health", "score": 1.0, "weight": 0.5, "passed": True, "details": "up"}'
            ']}, sys.stdout)\n'
        )
        result = await run_eval(f"{sys.executable} {script}", tmp_path, threshold=0.0)
        names = {r.name for r in result.results}
        # 12 mandatory + 2 project additions
        assert "ui_renders" in names
        assert "api_health" in names
        assert len(result.results) >= 14

    async def test_project_cannot_override_mandatory(self, tmp_path):
        """If project eval returns a dimension with the same name as mandatory, it's ignored."""
        script = tmp_path / "score.py"
        script.write_text(
            'import json, sys\n'
            'json.dump({"results": ['
            '{"name": "tests", "score": 0.0, "weight": 1.0, "passed": false, "details": "fake override"}'
            ']}, sys.stdout)\n'
        )
        result = await run_eval(f"{sys.executable} {script}", tmp_path, threshold=0.0)
        # The "tests" dimension should come from hygiene, not the project override
        test_results = [r for r in result.results if r.name == "tests"]
        assert len(test_results) == 1
        assert "fake override" not in test_results[0].details

    async def test_failed_project_eval_still_has_mandatory(self, tmp_path):
        """If project eval command fails, mandatory dimensions still run."""
        result = await run_eval("nonexistent_command_xyz", tmp_path, threshold=0.0)
        names = {r.name for r in result.results}
        # All 12 mandatory should still be present
        assert len(names) >= 12
        assert "tests" in names
        assert "capability_surface" in names

    async def test_threshold_applied_to_composite(self, tmp_path):
        """Composite score is checked against threshold."""
        result = await run_eval("true", tmp_path, threshold=0.99)
        # With neutral scores (0.5) for undetected tools, composite will be < 0.99
        assert result.passed is False

    async def test_timeout_project_eval(self, tmp_path):
        """Project eval timeout doesn't prevent mandatory dimensions."""
        script = tmp_path / "hang.py"
        script.write_text("import time\ntime.sleep(60)\n")
        result = await run_eval(f"{sys.executable} {script}", tmp_path, threshold=0.0, timeout=1.0)
        # Mandatory dimensions still computed
        names = {r.name for r in result.results}
        assert len(names) >= 12

    async def test_weight_split_is_50_50(self, tmp_path):
        """Hygiene dimensions get 50% total weight, growth gets 50%."""
        result = await run_eval("true", tmp_path, threshold=0.0)
        hygiene_names = {"tests", "lint", "type_check", "coverage", "guard_patterns", "config_parser"}
        growth_names = {
            "capability_surface", "experiment_diversity", "observability",
            "research_grounding", "factory_effectiveness", "spec_compliance",
        }
        hygiene_weight = sum(r.weight for r in result.results if r.name in hygiene_names)
        growth_weight = sum(r.weight for r in result.results if r.name in growth_names)
        assert abs(hygiene_weight - 0.50) < 0.01
        assert abs(growth_weight - 0.50) < 0.01

    async def test_accepts_test_timeout_parameter(self, tmp_path):
        """run_eval should accept test_timeout parameter for hygiene test runs."""
        result = await run_eval("true", tmp_path, threshold=0.0, test_timeout=900)
        names = {r.name for r in result.results}
        assert "tests" in names
        assert len(names) >= 12
