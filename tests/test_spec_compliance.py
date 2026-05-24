"""Tests for spec_compliance growth dimension edge cases."""

import json
import time

from factory.eval.growth import eval_spec_compliance


class TestSpecComplianceMissing:
    def test_missing_file_returns_neutral(self, tmp_path):
        result = eval_spec_compliance(tmp_path)
        assert result["name"] == "spec_compliance"
        assert result["score"] == 0.5
        assert result["passed"] is True
        assert "not found" in result["details"]

    def test_missing_factory_dir_returns_neutral(self, tmp_path):
        result = eval_spec_compliance(tmp_path / "nonexistent")
        assert result["score"] == 0.5


class TestSpecComplianceStale:
    def test_stale_file_returns_neutral(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        spec_path = factory_dir / "spec_results.json"
        spec_path.write_text(json.dumps({"results": [], "total": 3, "passed": 3}))
        # Set mtime to 25 hours ago
        old_time = time.time() - 90000
        import os
        os.utime(spec_path, (old_time, old_time))

        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 0.5
        assert "stale" in result["details"]


class TestSpecComplianceScoring:
    def test_all_passed(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text(json.dumps({
            "results": [
                {"name": "check 1", "passed": True},
                {"name": "check 2", "passed": True},
            ],
            "total": 2,
            "passed": 2,
        }))
        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 1.0
        assert result["passed"] is True
        assert "2/2" in result["details"]

    def test_partial_pass(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text(json.dumps({
            "results": [
                {"name": "check 1", "passed": True},
                {"name": "check 2", "passed": False},
                {"name": "check 3", "passed": False},
            ],
            "total": 3,
            "passed": 1,
        }))
        result = eval_spec_compliance(tmp_path)
        assert abs(result["score"] - 1 / 3) < 1e-4
        assert result["passed"] is False

    def test_all_failed(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text(json.dumps({
            "results": [{"name": "check 1", "passed": False}],
            "total": 1,
            "passed": 0,
        }))
        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 0.0
        assert result["passed"] is False

    def test_zero_total_returns_neutral(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text(json.dumps({
            "results": [],
            "total": 0,
            "passed": 0,
        }))
        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 0.5
        assert "No spec checks" in result["details"]


class TestSpecComplianceErrors:
    def test_invalid_json_returns_neutral(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text("{bad json")
        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 0.5
        assert "Error" in result["details"]

    def test_missing_keys_returns_neutral(self, tmp_path):
        factory_dir = tmp_path / ".factory"
        factory_dir.mkdir()
        (factory_dir / "spec_results.json").write_text(json.dumps({}))
        result = eval_spec_compliance(tmp_path)
        assert result["score"] == 0.5
