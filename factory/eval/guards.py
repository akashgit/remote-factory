"""Guard rules — safety checks that must pass before any change is kept."""

import fnmatch
import subprocess
from datetime import datetime
from pathlib import Path, PurePath


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def check_eval_immutable(project_path: Path, tree_before: str) -> str | None:
    """Guard: eval/ directory must not be modified by the change.

    Compare git ls-tree snapshot taken before the change with current state.
    Returns a violation string if eval/ was modified, None otherwise.
    """
    try:
        tree_after = _run_git(["ls-tree", "HEAD", "eval/"], project_path)
    except subprocess.CalledProcessError:
        tree_after = ""

    if tree_before != tree_after:
        return "eval/ directory was modified"
    return None


_AUTO_GENERATED_FILES = {"uv.lock", "package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock"}


def check_git_clean(project_path: Path) -> str | None:
    """Guard: working tree must be clean (no uncommitted changes).

    Ignores auto-generated lock files that tools like ``uv run`` may
    touch as a side effect of running commands.
    Returns a violation string if dirty, None otherwise.
    """
    status = _run_git(["status", "--porcelain"], project_path)
    if not status:
        return None
    significant = [
        line for line in status.splitlines()
        if not line.startswith("??")
        and line.lstrip(" MADRCU?!").split("/")[-1] not in _AUTO_GENERATED_FILES
    ]
    if significant:
        return f"Working tree is dirty: {' '.join(significant)}"
    return None


def check_experiment_branch(project_path: Path, baseline_sha: str) -> str | None:
    """Guard: changes must be on an experiment branch from baseline.

    More flexible than single-commit — allows multiple commits but verifies
    the branch diverged from the expected baseline.
    Returns a violation string if the branch is not rooted at baseline, None otherwise.
    """
    try:
        merge_base = _run_git(["merge-base", baseline_sha, "HEAD"], project_path)
    except subprocess.CalledProcessError:
        return f"Cannot find merge-base between {baseline_sha} and HEAD"

    if merge_base != baseline_sha:
        return f"Branch is not rooted at baseline {baseline_sha} (merge-base: {merge_base})"

    # Verify at least one commit exists
    log_output = _run_git(["log", "--oneline", f"{baseline_sha}..HEAD"], project_path)
    if not log_output.strip():
        return "No commits between baseline and HEAD"

    return None


def _glob_match(filepath: str, pattern: str) -> bool:
    """Match a filepath against a glob pattern, supporting ** for recursive matching."""
    if "**" in pattern:
        # Split on first ** occurrence to get prefix and suffix
        parts = pattern.split("**", 1)
        prefix, suffix = parts
        # Remove trailing slash from prefix
        prefix = prefix.rstrip("/")
        # Remove leading slash from suffix
        suffix = suffix.lstrip("/")

        if prefix and not filepath.startswith(prefix + "/"):
            return False

        remaining = filepath[len(prefix):].lstrip("/") if prefix else filepath

        if suffix:
            # suffix is a pattern like "*.py" — match it against the filename
            # or any sub-path within the remaining path
            return fnmatch.fnmatch(remaining, suffix) or fnmatch.fnmatch(
                remaining, "*/" + suffix
            )
        # No suffix: ** at end matches everything under the prefix
        return True

    # For non-** patterns, use PurePath.match which correctly treats * as
    # not crossing directory separators
    return PurePath(filepath).match(pattern)


def check_scope(project_path: Path, baseline_sha: str, allowed_scope: list[str]) -> str | None:
    """Guard: changed files must be within the declared scope.

    Uses fnmatch-style patterns from factory.md scope.
    Returns a violation string if files outside scope were modified, None otherwise.
    """
    try:
        diff_output = _run_git(["diff", "--name-only", f"{baseline_sha}..HEAD"], project_path)
    except subprocess.CalledProcessError:
        return "Cannot determine changed files"

    if not diff_output.strip():
        return None

    changed_files = diff_output.strip().splitlines()
    out_of_scope: list[str] = []

    for changed_file in changed_files:
        basename = changed_file.split("/")[-1]
        if basename in _AUTO_GENERATED_FILES:
            continue
        matched = any(_glob_match(changed_file, pattern) for pattern in allowed_scope)
        if not matched:
            out_of_scope.append(changed_file)

    if out_of_scope:
        return f"Files outside scope: {', '.join(out_of_scope)}"
    return None


def check_fixed_surfaces(
    project_path: Path, baseline_sha: str, fixed_surfaces: list[str]
) -> str | None:
    """Guard: changed files must not be in the fixed_surfaces list.

    Fixed surfaces are files that must never be modified (e.g. ground truth,
    test data, eval infrastructure). Uses the same glob matching as check_scope.
    Returns a violation string if fixed surfaces were modified, None otherwise.
    """
    try:
        diff_output = _run_git(["diff", "--name-only", f"{baseline_sha}..HEAD"], project_path)
    except subprocess.CalledProcessError:
        return "Cannot determine changed files"

    if not diff_output.strip():
        return None

    changed_files = diff_output.strip().splitlines()
    violated: list[str] = []

    for changed_file in changed_files:
        basename = changed_file.split("/")[-1]
        if basename in _AUTO_GENERATED_FILES:
            continue
        if any(_glob_match(changed_file, pattern) for pattern in fixed_surfaces):
            violated.append(changed_file)

    if violated:
        return f"Fixed surface modified: {', '.join(violated)}"
    return None


_TEST_FILE_PATTERNS = ("test_", "_test.", "/tests/")


def _is_test_file(filepath: str) -> bool:
    """Check if a filepath looks like a test file."""
    basename = filepath.split("/")[-1]
    return (
        basename.startswith("test_")
        or "_test." in basename
        or "/tests/" in ("/" + filepath)
    )


def check_test_deletion(project_path: Path, baseline_sha: str) -> str | None:
    """Guard: test files must not be deleted.

    Runs ``git diff --name-status`` between baseline and HEAD, filters for
    deleted (D) entries that match test file patterns. Returns a violation
    string listing deleted test files, or None if clean.
    """
    try:
        diff_output = _run_git(
            ["diff", "--name-status", f"{baseline_sha}..HEAD"], project_path,
        )
    except subprocess.CalledProcessError:
        return "Cannot determine deleted files (git diff failed)"

    if not diff_output.strip():
        return None

    deleted_tests: list[str] = []
    for line in diff_output.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, filepath = parts
        if status != "D":
            continue
        basename = filepath.split("/")[-1]
        if basename in _AUTO_GENERATED_FILES:
            continue
        if _is_test_file(filepath):
            deleted_tests.append(filepath)

    if deleted_tests:
        return f"Test files deleted: {', '.join(deleted_tests)}"
    return None


def check_eval_not_skipped(
    project_path: Path,
    cycle_started_at: datetime | None = None,
) -> str | None:
    """Guard: eval must have been run during the current cycle.

    Checks that ``.factory/last_eval.json`` exists and its modification time
    is after ``cycle_started_at``. If ``cycle_started_at`` is None, only
    checks existence.
    """
    last_eval = project_path / ".factory" / "last_eval.json"
    if not last_eval.is_file():
        return "Eval was skipped: .factory/last_eval.json does not exist"

    if cycle_started_at is not None:
        mtime = datetime.fromtimestamp(last_eval.stat().st_mtime)
        if mtime < cycle_started_at:
            return (
                f"Eval was skipped: .factory/last_eval.json is stale "
                f"(modified {mtime.isoformat()}, cycle started {cycle_started_at.isoformat()})"
            )

    return None


def snapshot_eval_tree(project_path: Path) -> str:
    """Take a snapshot of eval/ tree for later comparison."""
    try:
        return _run_git(["ls-tree", "HEAD", "eval/"], project_path)
    except subprocess.CalledProcessError:
        return ""


def check_all(
    project_path: Path,
    baseline_sha: str,
    eval_tree_before: str | None = None,
    allowed_scope: list[str] | None = None,
    fixed_surfaces: list[str] | None = None,
    cycle_started_at: datetime | None = None,
) -> list[str]:
    """Run all guards, return list of violation strings (empty = pass)."""
    violations: list[str] = []

    if eval_tree_before is not None:
        v = check_eval_immutable(project_path, eval_tree_before)
        if v:
            violations.append(v)

    v = check_git_clean(project_path)
    if v:
        violations.append(v)

    v = check_experiment_branch(project_path, baseline_sha)
    if v:
        violations.append(v)

    if allowed_scope is not None:
        v = check_scope(project_path, baseline_sha, allowed_scope)
        if v:
            violations.append(v)

    if fixed_surfaces:
        v = check_fixed_surfaces(project_path, baseline_sha, fixed_surfaces)
        if v:
            violations.append(v)

    v = check_test_deletion(project_path, baseline_sha)
    if v:
        violations.append(v)

    if cycle_started_at is not None:
        v = check_eval_not_skipped(project_path, cycle_started_at)
        if v:
            violations.append(v)

    return violations
