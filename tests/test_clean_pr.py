"""Tests for factory/clean_pr.py — include/exclude filtering and strip logic."""

from __future__ import annotations

from factory.clean_pr import DEFAULT_EXCLUDES, _glob_match, filter_pr_diff


class TestGlobMatch:
    def test_exact_match(self) -> None:
        assert _glob_match("eval/score.py", "eval/score.py")

    def test_no_match(self) -> None:
        assert not _glob_match("src/main.py", "eval/score.py")

    def test_double_star_prefix(self) -> None:
        assert _glob_match(".factory/config.json", ".factory/**")
        assert _glob_match(".factory/experiments/001/verdict.json", ".factory/**")

    def test_double_star_with_suffix(self) -> None:
        assert _glob_match("benchmarks/run.py", "benchmarks/**")
        assert _glob_match("benchmarks/deep/nested/file.txt", "benchmarks/**")

    def test_wildcard_pattern(self) -> None:
        assert _glob_match("tests/eval_runner.py", "tests/eval_*")
        assert not _glob_match("tests/test_main.py", "tests/eval_*")

    def test_star_py(self) -> None:
        assert _glob_match("src/utils.py", "src/*.py")
        assert not _glob_match("src/deep/utils.py", "src/*.py")


class TestFilterPrDiff:
    def test_default_excludes_applied(self) -> None:
        files = [
            "src/main.py",
            "eval/score.py",
            ".factory/config.json",
            "benchmarks/run.sh",
            "tests/eval_runner.py",
            "tests/test_main.py",
        ]
        keep, strip = filter_pr_diff(files)
        assert "src/main.py" in keep
        assert "tests/test_main.py" in keep
        assert "eval/score.py" in strip
        assert ".factory/config.json" in strip
        assert "benchmarks/run.sh" in strip
        assert "tests/eval_runner.py" in strip

    def test_custom_exclude(self) -> None:
        files = ["src/main.py", "docs/README.md"]
        keep, strip = filter_pr_diff(files, exclude=["docs/**"])
        assert keep == ["src/main.py"]
        assert strip == ["docs/README.md"]

    def test_include_filter(self) -> None:
        files = ["src/main.py", "src/utils.py", "config/settings.toml"]
        keep, strip = filter_pr_diff(files, include=["src/**"])
        assert keep == ["src/main.py", "src/utils.py"]
        assert strip == ["config/settings.toml"]

    def test_exclude_wins_over_include(self) -> None:
        files = ["eval/score.py", "eval/helpers.py"]
        keep, strip = filter_pr_diff(files, include=["eval/**"])
        assert "eval/helpers.py" in keep
        assert "eval/score.py" in strip

    def test_empty_include_keeps_all(self) -> None:
        files = ["src/main.py", "lib/util.py"]
        keep, strip = filter_pr_diff(files, include=[])
        assert keep == ["src/main.py", "lib/util.py"]
        assert strip == []

    def test_empty_files(self) -> None:
        keep, strip = filter_pr_diff([])
        assert keep == []
        assert strip == []

    def test_composability(self) -> None:
        files = [
            "src/app.py",
            "src/test_helper.py",
            "docs/guide.md",
            ".factory/results.tsv",
        ]
        keep, strip = filter_pr_diff(
            files,
            include=["src/**", "docs/**"],
            exclude=["docs/**"],
        )
        assert "src/app.py" in keep
        assert "src/test_helper.py" in keep
        assert "docs/guide.md" in strip
        assert ".factory/results.tsv" in strip

    def test_overlapping_patterns(self) -> None:
        files = ["tests/eval_smoke.py", "tests/test_eval.py"]
        keep, strip = filter_pr_diff(files)
        assert "tests/test_eval.py" in keep
        assert "tests/eval_smoke.py" in strip


class TestDefaultExcludes:
    def test_default_excludes_are_present(self) -> None:
        assert "eval/score.py" in DEFAULT_EXCLUDES
        assert "benchmarks/**" in DEFAULT_EXCLUDES
        assert "tests/eval_*" in DEFAULT_EXCLUDES
        assert ".factory/**" in DEFAULT_EXCLUDES
