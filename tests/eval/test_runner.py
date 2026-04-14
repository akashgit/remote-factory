"""Tests for factory.eval.runner — eval subprocess execution."""

import pytest

from factory.eval.runner import run_eval


class TestRunEval:
    async def test_successful_eval(self, tmp_path):
        """Valid eval script that outputs correct JSON."""
        script = tmp_path / "score.py"
        script.write_text(
            'import json, sys\n'
            'json.dump({"results": ['
            '{"name": "tests", "score": 0.9, "weight": 0.6, "passed": True, "details": "ok"},'
            '{"name": "lint", "score": 1.0, "weight": 0.4, "passed": True, "details": "clean"}'
            ']}, sys.stdout)\n'
        )
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8)
        assert result.total > 0.0
        # 2 project + 5 growth dimensions
        assert len(result.results) == 7
        project_names = {r.name for r in result.results[:2]}
        assert project_names == {"tests", "lint"}

    async def test_command_not_found(self, tmp_path):
        """Non-existent command returns error score."""
        result = await run_eval("nonexistent_command_xyz", tmp_path, threshold=0.8)
        assert result.passed is False
        assert result.total == 0.0
        assert result.results[0].name == "error"

    async def test_timeout(self, tmp_path):
        """Script that hangs is killed after timeout."""
        script = tmp_path / "hang.py"
        script.write_text("import time\ntime.sleep(60)\n")
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8, timeout=1.0)
        assert result.passed is False
        assert result.total == 0.0
        assert "Timeout" in result.results[0].details

    async def test_nonzero_exit(self, tmp_path):
        """Script that exits with non-zero returns error score."""
        script = tmp_path / "fail.py"
        script.write_text("import sys\nsys.exit(1)\n")
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8)
        assert result.passed is False
        assert result.total == 0.0
        assert "exit code" in result.results[0].details.lower()

    async def test_invalid_json(self, tmp_path):
        """Script that outputs non-JSON returns error score."""
        script = tmp_path / "bad.py"
        script.write_text('print("not json at all")\n')
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8)
        assert result.passed is False
        assert result.total == 0.0
        assert result.results[0].name == "error"

    async def test_malformed_results(self, tmp_path):
        """Script that outputs JSON without proper results array."""
        script = tmp_path / "malformed.py"
        script.write_text(
            'import json, sys\n'
            'json.dump({"results": [{"wrong": "keys"}]}, sys.stdout)\n'
        )
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8)
        assert result.passed is False
        assert result.total == 0.0

    async def test_threshold_failure(self, tmp_path):
        """Score below threshold means passed=False."""
        script = tmp_path / "low.py"
        script.write_text(
            'import json, sys\n'
            'json.dump({"results": ['
            '{"name": "tests", "score": 0.3, "weight": 1.0, "passed": False, "details": "failing"}'
            ']}, sys.stdout)\n'
        )
        result = await run_eval(f"python {script}", tmp_path, threshold=0.8)
        assert result.passed is False
        # Project score is 0.3 but gets 50% weight; growth adds ~0.25
        # Total should be well below the 0.8 threshold
        assert result.total < 0.8
