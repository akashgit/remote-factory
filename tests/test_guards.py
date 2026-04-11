"""Tests for factory.eval.guards — safety checks."""

import subprocess
from pathlib import Path

import pytest

from factory.eval.guards import (
    check_eval_immutable,
    check_experiment_branch,
    check_git_clean,
    check_scope,
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


class TestCheckAll:
    def test_clean_passes(self, git_project):
        baseline = _git(["rev-parse", "HEAD"], git_project).stdout.strip()
        tree = snapshot_eval_tree(git_project)
        (git_project / "src" / "new.py").write_text("new\n")
        _git(["add", "."], git_project)
        _git(["commit", "-m", "change"], git_project)
        violations = check_all(git_project, baseline, eval_tree_before=tree)
        assert violations == []
