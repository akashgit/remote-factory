"""Tests for factory.eval.guards — safety checks."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.eval.guards import (
    _glob_match,
    check_eval_immutable,
    check_eval_not_skipped,
    check_experiment_branch,
    check_fixed_surfaces,
    check_git_clean,
    check_scope,
    check_test_deletion,
    snapshot_eval_tree,
    check_all,
)


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(cwd.parent),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        check=True, env=env, **kwargs,
    )


@pytest.fixture
def git_project(tmp_path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    _git(["init"], project)
    (project / "src").mkdir()
    (project / "src" / "main.py").write_text("print('hello')\n")
    _git(["add", "."], project)
    _git(["commit", "-m", "initial"], project)
    return project


class TestCheckGitClean:
    def test_clean_repo(self, git_project):
        assert check_git_clean(git_project) is None

    def test_dirty_repo(self, git_project):
        (git_project / "src" / "main.py").write_text("changed\n")
        result = check_git_clean(git_project)
        assert result is not None
        assert "dirty" in result.lower()

    def test_ignores_lock_files(self, git_project):
        (git_project / "uv.lock").write_text("modified\n")
        result = check_git_clean(git_project)
        assert result is None

    def test_dirty_with_lock_and_code(self, git_project):
        (git_project / "uv.lock").write_text("modified\n")
        (git_project / "src" / "main.py").write_text("changed\n")
        result = check_git_clean(git_project)
        assert result is not None
        assert "dirty" in result.lower()


class TestCheckEvalImmutable:
    def test_unchanged_eval(self, git_project):
        tree = snapshot_eval_tree(git_project)
        assert check_eval_immutable(git_project, tree) is None

    def test_modified_eval(self, git_project):
        tree = snapshot_eval_tree(git_project)
        (git_project / "eval").mkdir()
        (git_project / "eval" / "score.py").write_text("print('eval')\n")
        _git(["add", "eval/"], git_project)
        _git(["commit", "-m", "add eval"], git_project)
        assert check_eval_immutable(git_project, tree) is not None


class TestCheckExperimentBranch:
    def test_valid_branch(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "experiment"], git_project)
        assert check_experiment_branch(git_project, baseline) is None

    def test_no_commits(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        result = check_experiment_branch(git_project, baseline)
        assert result is not None
        assert "No commits" in result

    def test_multiple_commits_ok(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "a.py").write_text("a\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "change 1"], git_project)
        (git_project / "src" / "b.py").write_text("b\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "change 2"], git_project)
        assert check_experiment_branch(git_project, baseline) is None


class TestCheckScope:
    def test_in_scope(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "in scope"], git_project)
        assert check_scope(git_project, baseline, ["src/**/*.py"]) is None

    def test_out_of_scope(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "README.md").write_text("changed\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "out of scope"], git_project)
        result = check_scope(git_project, baseline, ["src/**/*.py"])
        assert result is not None
        assert "README.md" in result

    def test_no_changes(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        assert check_scope(git_project, baseline, ["src/**/*.py"]) is None


class TestGlobMatch:
    """Tests for _glob_match with ** and * patterns."""

    @pytest.mark.parametrize(
        "pattern, filepath, expected",
        [
            # ** matching across directories
            ("factory/**/*.py", "factory/eval/runner.py", True),
            ("factory/**/*.py", "factory/agents/prompts/builder.md", False),
            ("factory/**/*.py", "tests/test_guards.py", False),
            ("tests/**/*.py", "tests/test_guards.py", True),
            ("tests/**/*.py", "tests/eval/test_runner.py", True),
            # ** at the beginning
            ("**/*.md", "README.md", True),
            ("**/*.md", "factory/agents/prompts/builder.md", True),
            # ** at the end (match everything under prefix)
            ("templates/**", "templates/factory_config.md", True),
            # Single * (no directory crossing)
            ("factory/agents/prompts/*.md", "factory/agents/prompts/builder.md", True),
            ("factory/agents/prompts/*.md", "factory/agents/prompts/sub/nested.md", False),
        ],
    )
    def test_glob_patterns(self, pattern: str, filepath: str, expected: bool):
        assert _glob_match(filepath, pattern) is expected


class TestCheckFixedSurfaces:
    def test_no_violation_safe_files(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "safe change"], git_project)
        result = check_fixed_surfaces(git_project, baseline, ["data/**", "ground_truth.json"])
        assert result is None

    def test_violation_fixed_surface_modified(self, git_project):
        (git_project / "ground_truth.json").write_text("{}\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add truth"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "ground_truth.json").write_text('{"answer": 42}\n')
        _git(["add", "."], git_project)
        _git(["commit", "-m", "modify truth"], git_project)
        result = check_fixed_surfaces(git_project, baseline, ["ground_truth.json"])
        assert result is not None
        assert "ground_truth.json" in result

    def test_lock_files_ignored(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "uv.lock").write_text("lock content\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "lock change"], git_project)
        result = check_fixed_surfaces(git_project, baseline, ["**"])
        assert result is None

    def test_glob_patterns(self, git_project):
        (git_project / "data").mkdir()
        (git_project / "data" / "expected.json").write_text("{}\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add data"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "data" / "expected.json").write_text('{"changed": true}\n')
        _git(["add", "."], git_project)
        _git(["commit", "-m", "modify data"], git_project)
        result = check_fixed_surfaces(git_project, baseline, ["data/**/*.json"])
        assert result is not None
        assert "expected.json" in result

    def test_no_changes(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        result = check_fixed_surfaces(git_project, baseline, ["data/**"])
        assert result is None


class TestCheckAll:
    def test_clean_passes(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        tree = snapshot_eval_tree(git_project)
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "change"], git_project)
        violations = check_all(git_project, baseline, eval_tree_before=tree)
        assert violations == []

    def test_fixed_surfaces_wired(self, git_project):
        (git_project / "truth.json").write_text("{}\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add truth"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "truth.json").write_text('{"modified": true}\n')
        _git(["add", "."], git_project)
        _git(["commit", "-m", "modify truth"], git_project)
        violations = check_all(
            git_project, baseline,
            fixed_surfaces=["truth.json"],
        )
        assert any("Fixed surface" in v for v in violations)


class TestCheckTestDeletion:
    def test_detects_deleted_test_file(self, git_project):
        (git_project / "tests").mkdir()
        (git_project / "tests" / "test_foo.py").write_text("def test_x(): pass\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add test"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "tests" / "test_foo.py").unlink()
        _git(["add", "."], git_project)
        _git(["commit", "-m", "delete test"], git_project)
        result = check_test_deletion(git_project, baseline)
        assert result is not None
        assert "test_foo.py" in result

    def test_ignores_non_test_files(self, git_project):
        (git_project / "src" / "utils.py").write_text("x = 1\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add utils"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "utils.py").unlink()
        _git(["add", "."], git_project)
        _git(["commit", "-m", "delete utils"], git_project)
        result = check_test_deletion(git_project, baseline)
        assert result is None

    def test_detects_test_in_nested_dir(self, git_project):
        (git_project / "tests").mkdir()
        (git_project / "tests" / "unit").mkdir()
        (git_project / "tests" / "unit" / "test_bar.py").write_text("def test_y(): pass\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add nested test"], git_project)
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "tests" / "unit" / "test_bar.py").unlink()
        _git(["add", "."], git_project)
        _git(["commit", "-m", "delete nested test"], git_project)
        result = check_test_deletion(git_project, baseline)
        assert result is not None
        assert "test_bar.py" in result

    def test_clean_when_no_deletions(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "add file"], git_project)
        result = check_test_deletion(git_project, baseline)
        assert result is None


class TestCheckEvalNotSkipped:
    def test_passes_when_recent(self, git_project):
        factory_dir = git_project / ".factory"
        factory_dir.mkdir()
        last_eval = factory_dir / "last_eval.json"
        last_eval.write_text(json.dumps({"total": 0.8}))
        cycle_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = check_eval_not_skipped(git_project, cycle_started_at)
        assert result is None

    def test_fails_when_missing(self, git_project):
        result = check_eval_not_skipped(git_project)
        assert result is not None
        assert "does not exist" in result

    def test_check_eval_not_skipped_with_tz_aware_cycle(self, git_project):
        factory_dir = git_project / ".factory"
        factory_dir.mkdir()
        last_eval = factory_dir / "last_eval.json"
        last_eval.write_text(json.dumps({"total": 0.8}))
        cycle_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = check_eval_not_skipped(git_project, cycle_started_at)
        assert result is None

    def test_fails_when_stale(self, git_project):
        factory_dir = git_project / ".factory"
        factory_dir.mkdir()
        last_eval = factory_dir / "last_eval.json"
        last_eval.write_text(json.dumps({"total": 0.8}))
        import os
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        os.utime(last_eval, (stale_time, stale_time))
        cycle_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = check_eval_not_skipped(git_project, cycle_started_at)
        assert result is not None
        assert "stale" in result
