"""Tests for factory/clean_pr.py — include/exclude filtering and strip logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

from factory.clean_pr import DEFAULT_EXCLUDES, _glob_match, filter_pr_diff, strip_pr_artifacts


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


class TestStripPrArtifacts:
    """Tests for strip_pr_artifacts using a real temporary git repo."""

    def _init_repo(self, tmp_path: Path) -> Path:
        """Create a git repo with a main branch and a feature branch."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
        )
        subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True, check=True)
        return repo

    def _add_file(self, repo: Path, path: str, content: str = "x") -> None:
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        subprocess.run(["git", "add", path], cwd=repo, capture_output=True, check=True)

    def _commit(self, repo: Path, msg: str = "c") -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@test",
             "commit", "-m", msg],
            cwd=repo, capture_output=True, check=True,
        )

    def test_normal_strip(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        self._add_file(repo, "src/main.py", "print('hello')")
        self._commit(repo, "add src")

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True, check=True)
        self._add_file(repo, "src/main.py", "print('updated')")
        self._add_file(repo, ".factory/config.json", "{}")
        self._commit(repo, "feature changes")

        keep, stripped = strip_pr_artifacts(repo, base_branch="main")
        assert "src/main.py" in keep
        assert ".factory/config.json" in stripped

    def test_new_file_removal(self, tmp_path: Path) -> None:
        """New files (not on base branch) should be git rm'd, not git checkout'd."""
        repo = self._init_repo(tmp_path)
        self._add_file(repo, "src/main.py", "x")
        self._commit(repo, "base")

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True, check=True)
        self._add_file(repo, "src/main.py", "updated")
        self._add_file(repo, "benchmarks/new_bench.py", "bench")
        self._commit(repo, "feature")

        keep, stripped = strip_pr_artifacts(repo, base_branch="main")
        assert "src/main.py" in keep
        assert "benchmarks/new_bench.py" in stripped
        assert not (repo / "benchmarks" / "new_bench.py").exists()

    def test_git_diff_error(self, tmp_path: Path) -> None:
        """When git diff fails, return empty lists gracefully."""
        repo = self._init_repo(tmp_path)
        keep, stripped = strip_pr_artifacts(repo, base_branch="nonexistent-branch")
        assert keep == []
        assert stripped == []

    def test_empty_strip_list(self, tmp_path: Path) -> None:
        """When nothing needs stripping, no files are modified."""
        repo = self._init_repo(tmp_path)
        self._add_file(repo, "src/main.py", "x")
        self._commit(repo, "base")

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True, check=True)
        self._add_file(repo, "src/main.py", "updated")
        self._commit(repo, "feature")

        keep, stripped = strip_pr_artifacts(repo, base_branch="main")
        assert keep == ["src/main.py"]
        assert stripped == []

    def test_no_changes(self, tmp_path: Path) -> None:
        """When there are no changes vs base, return empty lists."""
        repo = self._init_repo(tmp_path)
        self._add_file(repo, "src/main.py", "x")
        self._commit(repo, "base")

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True, check=True)
        keep, stripped = strip_pr_artifacts(repo, base_branch="main")
        assert keep == []
        assert stripped == []

    def test_stages_only_specific_files(self, tmp_path: Path) -> None:
        """After stripping, only stripped files should be staged, not untracked files."""
        repo = self._init_repo(tmp_path)
        self._add_file(repo, "src/main.py", "x")
        self._commit(repo, "base")

        subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, capture_output=True, check=True)
        self._add_file(repo, "src/main.py", "updated")
        self._add_file(repo, ".factory/config.json", "{}")
        self._commit(repo, "feature")

        # Create an untracked file that should NOT be staged
        (repo / "untracked.txt").write_text("should not be staged")

        strip_pr_artifacts(repo, base_branch="main")

        # Check that untracked.txt is still untracked
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True,
        )
        untracked_lines = [line for line in status.stdout.splitlines() if "untracked.txt" in line]
        assert len(untracked_lines) == 1
        assert untracked_lines[0].startswith("??")


class TestDefaultExcludes:
    def test_default_excludes_are_present(self) -> None:
        assert "eval/score.py" in DEFAULT_EXCLUDES
        assert "benchmarks/**" in DEFAULT_EXCLUDES
        assert "tests/eval_*" in DEFAULT_EXCLUDES
        assert ".factory/**" in DEFAULT_EXCLUDES
