"""Tests for factory/worktree.py — git worktree lifecycle management."""

import subprocess
from pathlib import Path

import pytest

from factory.worktree import create_worktree, detect_default_branch, prune_stale, remove_worktree

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
        assert branch.startswith("factory/run-")
        assert wt_path.parent == git_project / ".factory-worktrees"

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

    def test_uses_provided_run_id(self, git_project: Path) -> None:
        uuid_str = "d854881a-800d-44ff-beb5-b9fd77cc3fb9"
        wt_path, branch = create_worktree(git_project, run_id=uuid_str)

        # First 8 chars of UUID should be used
        assert branch == "factory/run-d854881a"
        assert wt_path.name == "run-d854881a"

    def test_run_id_truncated_to_8_chars(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project, run_id="abcdef1234567890")

        assert branch == "factory/run-abcdef12"
        assert wt_path.name == "run-abcdef12"

    def test_short_run_id_used_as_is(self, git_project: Path) -> None:
        wt_path, branch = create_worktree(git_project, run_id="abc")

        assert branch == "factory/run-abc"
        assert wt_path.name == "run-abc"

    def test_multiple_worktrees_coexist(self, git_project: Path) -> None:
        wt1, br1 = create_worktree(git_project)
        wt2, br2 = create_worktree(git_project)

        assert wt1 != wt2
        assert br1 != br2
        assert wt1.exists()
        assert wt2.exists()


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
        wt_dir = git_project / ".factory-worktrees"
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
            assert branch.startswith("factory/run-")
            assert (wt_path / "README.md").exists()
        finally:
            remove_worktree(git_project_master, wt_path, branch)


class TestSHAResolution:
    def test_create_worktree_resolves_head(self, git_project: Path) -> None:
        """create_worktree('HEAD') resolves to the current commit SHA."""
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_project, capture_output=True, text=True, check=True,
        ).stdout.strip()

        wt_path, branch = create_worktree(git_project, "HEAD")

        wt_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=wt_path, capture_output=True, text=True, check=True,
        ).stdout.strip()

        assert wt_sha == expected_sha

    def test_create_worktree_resolves_amended_head(self, git_project: Path) -> None:
        """After an amend, create_worktree('HEAD') branches from the new commit."""
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(git_project.parent),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }

        (git_project / "new_file.txt").write_text("amended content")
        subprocess.run(["git", "add", "."], cwd=git_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--amend", "--no-edit"],
            cwd=git_project, capture_output=True, check=True, env=env,
        )
        amended_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_project, capture_output=True, text=True, check=True,
        ).stdout.strip()

        wt_path, branch = create_worktree(git_project, "HEAD")

        wt_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=wt_path, capture_output=True, text=True, check=True,
        ).stdout.strip()

        assert wt_sha == amended_sha
        assert (wt_path / "new_file.txt").exists()


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
