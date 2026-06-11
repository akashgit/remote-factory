"""Tests for factory/worktree.py — git worktree lifecycle management."""

import subprocess
from pathlib import Path

import pytest

from factory.worktree import (
    _classify_prefix,
    _slugify,
    create_worktree,
    detect_default_branch,
    prune_stale,
    remove_worktree,
)

pytestmark = pytest.mark.real_worktree


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a minimal git project with .factory/ directory."""
    project = tmp_path / "project"
    project.mkdir()

    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    subprocess.run(["git", "init", "-b", "main"], cwd=project, capture_output=True, check=True)
    (project / ".gitignore").write_text(".factory/\n")
    (project / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, capture_output=True, check=True, env=env,
    )

    factory_dir = project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text("{}")
    (factory_dir / "results.tsv").write_text("id\n")

    return project


class TestCreateWorktree:
    def test_creates_worktree_dir(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)

        assert wt_path.exists()
        assert wt_path.is_dir()
        assert branch.startswith("factory/")
        assert wt_path.parent == git_project / ".factory" / "worktrees"

    def test_worktree_has_factory_symlink(self, git_project: Path) -> None:
        wt_path, _ = create_worktree(git_project)

        symlink = wt_path / ".factory"
        assert symlink.is_symlink()
        assert symlink.resolve() == (git_project / ".factory").resolve()

    def test_worktree_contains_project_files(self, git_project: Path) -> None:
        wt_path, _ = create_worktree(git_project)

        assert (wt_path / "README.md").exists()
        assert (wt_path / "README.md").read_text() == "hello"

    def test_worktree_branch_is_checked_out(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)

        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path, capture_output=True, text=True,
        )
        assert result.stdout.strip() == branch

    def test_worktree_uses_custom_base_branch(self, git_project: Path) -> None:
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(git_project.parent),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        subprocess.run(
            ["git", "checkout", "-b", "develop"],
            cwd=git_project, capture_output=True, check=True,
        )
        (git_project / "extra.txt").write_text("dev")
        subprocess.run(["git", "add", "."], cwd=git_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "dev commit"],
            cwd=git_project, capture_output=True, check=True, env=env,
        )
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=git_project, capture_output=True, check=True,
        )

        wt_path, _ = create_worktree(git_project, base_branch="develop")
        assert (wt_path / "extra.txt").exists()

    def test_multiple_worktrees_coexist(self, git_project: Path) -> None:
        wt1, br1 = create_worktree(git_project)
        wt2, br2 = create_worktree(git_project)

        assert wt1 != wt2
        assert br1 != br2
        assert wt1.exists()
        assert wt2.exists()


class TestSlugify:
    def test_basic_text(self) -> None:
        assert _slugify("dashboard UI") == "dashboard-ui"

    def test_unicode(self) -> None:
        assert _slugify("café résumé") == "cafe-resume"

    def test_empty_string(self) -> None:
        assert _slugify("") == "unnamed"

    def test_only_special_chars(self) -> None:
        assert _slugify("!!!") == "unnamed"

    def test_long_text_truncated(self) -> None:
        result = _slugify("a" * 100, max_length=20)
        assert len(result) <= 20

    def test_no_trailing_hyphen_after_truncation(self) -> None:
        result = _slugify("hello-world-this-is-long", max_length=11)
        assert not result.endswith("-")

    def test_mixed_case_and_symbols(self) -> None:
        assert _slugify("Fix Login Crash!!") == "fix-login-crash"


class TestClassifyPrefix:
    def test_fix_keywords(self) -> None:
        assert _classify_prefix("fix login crash") == "fix"
        assert _classify_prefix("bug in parser") == "fix"
        assert _classify_prefix("error handling") == "fix"

    def test_docs_keywords(self) -> None:
        assert _classify_prefix("update documentation") == "docs"
        assert _classify_prefix("add README section") == "docs"

    def test_refactor_keywords(self) -> None:
        assert _classify_prefix("refactor auth module") == "refactor"
        assert _classify_prefix("cleanup old code") == "refactor"

    def test_test_keywords(self) -> None:
        assert _classify_prefix("add test coverage") == "test"

    def test_chore_keywords(self) -> None:
        assert _classify_prefix("update CI config") == "chore"
        assert _classify_prefix("bump dependency versions") == "chore"

    def test_chore_from_mode(self) -> None:
        assert _classify_prefix("something random", mode="discover") == "chore"
        assert _classify_prefix("something random", mode="meta") == "chore"

    def test_default_feat(self) -> None:
        assert _classify_prefix("dashboard UI") == "feat"
        assert _classify_prefix("add new endpoint") == "feat"

    def test_fix_takes_priority(self) -> None:
        assert _classify_prefix("fix the test") == "fix"


class TestSemanticBranchNaming:
    def test_semantic_with_hint(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project, hint="dashboard UI", mode="improve")

        assert branch.startswith("factory/feat/dashboard-ui-")
        assert wt_path.exists()
        marker = wt_path.parent / (wt_path.name + ".branch")
        assert marker.read_text() == branch
        assert not (wt_path / ".factory_branch").exists()

    def test_fix_hint(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project, hint="fix login crash", mode="improve")

        assert branch.startswith("factory/fix/")
        assert "login-crash" in branch

    def test_fallback_without_hint(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)

        assert branch.startswith("factory/run-")
        marker = wt_path.parent / (wt_path.name + ".branch")
        assert marker.read_text() == branch
        assert not (wt_path / ".factory_branch").exists()

    def test_factory_branch_marker_exists(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project, hint="add feature")

        marker = wt_path.parent / (wt_path.name + ".branch")
        assert marker.is_file()
        assert marker.read_text() == branch
        assert not (wt_path / ".factory_branch").exists()


class TestPruneWithMarker:
    def test_prune_reads_factory_branch_marker(self, git_project: Path) -> None:
        wt_dir = git_project / ".factory" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)

        orphan = wt_dir / "feat-dashboard-ui-abcd"
        orphan.mkdir()
        (wt_dir / "feat-dashboard-ui-abcd.branch").write_text("factory/feat/dashboard-ui-abcd")
        (orphan / "some_file.txt").write_text("stale")

        subprocess.run(
            ["git", "branch", "factory/feat/dashboard-ui-abcd"],
            cwd=git_project, capture_output=True, check=True,
        )

        pruned = prune_stale(git_project)
        assert len(pruned) >= 1
        assert not orphan.exists()
        assert not (wt_dir / "feat-dashboard-ui-abcd.branch").exists()

        result = subprocess.run(
            ["git", "branch", "--list", "factory/feat/dashboard-ui-abcd"],
            cwd=git_project, capture_output=True, text=True,
        )
        assert "factory/feat/dashboard-ui-abcd" not in result.stdout

    def test_prune_falls_back_for_legacy_dirs(self, git_project: Path) -> None:
        wt_dir = git_project / ".factory" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        orphan = wt_dir / "run-deadbeef"
        orphan.mkdir()
        (orphan / "some_file.txt").write_text("stale")

        pruned = prune_stale(git_project)
        assert len(pruned) >= 1
        assert not orphan.exists()


class TestRemoveWorktree:
    def test_removes_worktree_completely(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)
        assert wt_path.exists()

        remove_worktree(git_project, wt_path, branch)

        assert not wt_path.exists()

        result = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=git_project, capture_output=True, text=True,
        )
        assert branch not in result.stdout

    def test_safe_on_already_removed_path(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)
        remove_worktree(git_project, wt_path, branch)
        remove_worktree(git_project, wt_path, branch)

    def test_removes_from_worktree_list(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)
        remove_worktree(git_project, wt_path, branch)

        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=git_project, capture_output=True, text=True,
        )
        assert str(wt_path) not in result.stdout


class TestPruneStale:
    def test_no_op_without_factory_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "no-factory"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)

        pruned = prune_stale(project)
        assert pruned == []

    def test_cleans_orphaned_directory(self, git_project: Path) -> None:
        wt_dir = git_project / ".factory" / "worktrees"
        wt_dir.mkdir(parents=True, exist_ok=True)
        orphan = wt_dir / "run-deadbeef"
        orphan.mkdir()
        (orphan / "some_file.txt").write_text("stale")

        pruned = prune_stale(git_project)
        assert len(pruned) >= 1
        assert not orphan.exists()

    def test_preserves_active_worktrees(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project)

        pruned = prune_stale(git_project)
        assert wt_path.exists()
        for msg in pruned:
            assert wt_path.name not in msg

    def test_crash_recovery_cleans_all_artifacts(self, git_project: Path) -> None:
        """Simulate a crash: create worktree, delete dir manually, then prune."""
        wt_path, branch = create_worktree(git_project)
        import shutil
        shutil.rmtree(wt_path)

        pruned = prune_stale(git_project)
        assert len(pruned) >= 1

        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=git_project, capture_output=True, text=True,
        )
        assert str(wt_path) not in result.stdout


@pytest.fixture
def git_project_master(tmp_path: Path) -> Path:
    """Create a minimal git project with 'master' as the default branch."""
    project = tmp_path / "project"
    project.mkdir()

    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }

    subprocess.run(["git", "init", "-b", "master"], cwd=project, capture_output=True, check=True)
    (project / ".gitignore").write_text(".factory/\n")
    (project / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project, capture_output=True, check=True, env=env,
    )

    factory_dir = project / ".factory"
    factory_dir.mkdir()
    (factory_dir / "config.json").write_text("{}")
    (factory_dir / "results.tsv").write_text("id\n")

    return project


class TestDetectDefaultBranch:
    def test_detects_main(self, git_project: Path) -> None:
        assert detect_default_branch(git_project) == "main"

    def test_detects_master(self, git_project_master: Path) -> None:
        assert detect_default_branch(git_project_master) == "master"

    def test_local_only_repo_no_origin(self, git_project: Path) -> None:
        result = detect_default_branch(git_project)
        assert result == "main"

    def test_fallback_to_current_branch(self, tmp_path: Path) -> None:
        """Repo with neither 'main' nor 'master' falls back to current HEAD."""
        project = tmp_path / "project"
        project.mkdir()

        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }

        subprocess.run(
            ["git", "init", "-b", "develop"],
            cwd=project, capture_output=True, check=True,
        )
        (project / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=project, capture_output=True, check=True, env=env,
        )

        assert detect_default_branch(project) == "develop"


class TestCreateWorktreeWithMaster:
    def test_create_worktree_on_master_repo(self, git_project_master: Path) -> None:
        wt_path, branch = create_worktree(git_project_master, base_branch="master")
        try:
            assert wt_path.exists()
            assert branch.startswith("factory/")
            assert (wt_path / "README.md").exists()
        finally:
            remove_worktree(git_project_master, wt_path, branch)


class TestSymlinkResolution:
    def test_store_resolves_through_symlink(self, git_project: Path) -> None:
        """ExperimentStore via worktree symlink writes to main .factory/."""
        from factory.store import ExperimentStore

        wt_path, _ = create_worktree(git_project)
        store = ExperimentStore(wt_path)

        assert store.factory_dir.resolve() == (git_project / ".factory").resolve()

    def test_config_readable_through_symlink(self, git_project: Path) -> None:
        wt_path, _ = create_worktree(git_project)

        config_via_symlink = (wt_path / ".factory" / "config.json").read_text()
        config_direct = (git_project / ".factory" / "config.json").read_text()
        assert config_via_symlink == config_direct


class TestFilelockConcurrency:
    def test_filelock_prevents_concurrent_begin(self, git_project: Path) -> None:
        """Two stores targeting the same .factory/ get sequential IDs under real thread contention."""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        from factory.store import ExperimentStore

        (git_project / ".factory" / "experiments").mkdir(exist_ok=True)
        (git_project / ".factory" / "results.tsv").write_text(
            "id\ttimestamp\thypothesis\tchange_summary\tissue_number\tpr_number\t"
            "score_before\tscore_after\tdelta\tverdict\tcost_usd\tnotes\tresearch_citations\n"
        )

        def begin_in_thread(hypothesis: str) -> int:
            loop = asyncio.new_event_loop()
            try:
                store = ExperimentStore(git_project)
                return loop.run_until_complete(store.begin(hypothesis))
            finally:
                loop.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(begin_in_thread, "hypothesis A")
            fut_b = pool.submit(begin_in_thread, "hypothesis B")
            id_a = fut_a.result()
            id_b = fut_b.result()

        assert id_a != id_b
        assert {id_a, id_b} == {1, 2}
