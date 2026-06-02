from factory.runners.types import UsageStats
from factory.runners.usage_ledger import log_usage, read_usage


class TestLogUsage:
    def test_writes_entry(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        usage = UsageStats(input_tokens=100, output_tokens=50, cost_usd=0.01)
        log_usage(project, "claude", "builder", usage)
        entries = read_usage(project)
        assert len(entries) == 1
        assert entries[0]["runner"] == "claude"
        assert entries[0]["role"] == "builder"
        assert entries[0]["input_tokens"] == 100

    def test_none_usage_noop(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        log_usage(project, "claude", "builder", None)
        assert not (project / ".factory" / "usage.jsonl").exists()

    def test_appends(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".factory").mkdir()
        u1 = UsageStats(input_tokens=100)
        u2 = UsageStats(input_tokens=200)
        log_usage(project, "claude", "builder", u1)
        log_usage(project, "codex", "reviewer", u2)
        entries = read_usage(project)
        assert len(entries) == 2

    def test_empty_file(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        assert read_usage(project) == []
