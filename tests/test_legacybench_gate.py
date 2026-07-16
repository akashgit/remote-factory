"""Tests for legacybench gate_verify hardening and executor reloop feedback."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from factory.workflow.contributed.legacybench.workflow import workflow as legacybench_workflow
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import Edge, VerdictType


def _get_gate_command() -> str:
    """Extract the evaluator_command string from the legacybench gate_verify node."""
    wf = legacybench_workflow()
    gate = wf.nodes["gate_verify"]
    return gate.evaluator_command


def _run_gate(project_path: Path) -> str:
    """Run the gate command in a subprocess and return stdout."""
    cmd = _get_gate_command().replace("{project_path}", str(project_path))
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def _init_git(project: Path, tmp_path: Path) -> None:
    """Initialize a git repo with an initial commit + a second commit with a file change."""
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=project, capture_output=True, check=True, env=env,
    )
    (project / "change.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-m", "builder change"],
        cwd=project, capture_output=True, check=True, env=env,
    )


class TestGateVerifyScript:
    """Tests 1-7: gate script behavior via subprocess."""

    def test_no_commits_fail(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True, env=env)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "initial"],
            cwd=project, capture_output=True, check=True, env=env,
        )
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        output = _run_gate(project)
        assert output.startswith("fail")
        assert "did not commit" in output

    def test_missing_builder_output_fail(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        output = _run_gate(project)
        assert output.startswith("fail")
        assert "builder output missing" in output

    def test_make_and_test_succeed_pass(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        (project / "Makefile").write_text(
            "all:\n\t@echo 'build ok'\n\ntest:\n\t@echo 'tests pass'\n"
        )
        output = _run_gate(project)
        assert output.startswith("pass")
        assert "compilation and tests succeeded" in output

    def test_make_fails_reloop(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        (project / "Makefile").write_text(
            "all:\n\t@echo 'compile error on line 42' && exit 1\n\ntest:\n\t@echo 'ok'\n"
        )
        output = _run_gate(project)
        assert output.startswith("reloop")
        assert "compilation failed" in output
        assert "compile error on line 42" in output

    def test_make_test_fails_reloop(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        (project / "Makefile").write_text(
            "all:\n\t@echo 'build ok'\n\ntest:\n\t@echo 'FAIL: assertion error' && exit 1\n"
        )
        output = _run_gate(project)
        assert output.startswith("reloop")
        assert "tests failed" in output
        assert "FAIL: assertion error" in output

    def test_no_makefile_reloop(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        output = _run_gate(project)
        assert output.startswith("reloop")
        assert "no Makefile found" in output

    def test_no_test_target_reloop(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _init_git(project, tmp_path)
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "reviews" / "builder-latest.md").write_text("done")
        (project / "Makefile").write_text("all:\n\t@echo 'build ok'\n")
        output = _run_gate(project)
        assert output.startswith("reloop")
        assert "no test target" in output


def _make_executor() -> WorkflowExecutor:
    """Build a minimal WorkflowExecutor with edge index for gate_verify."""
    wf = legacybench_workflow()
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow = wf
    executor.project_path = Path("/fake")
    executor.log = MagicMock()
    executor._edge_index: dict[str, list[Edge]] = {}
    for edge in wf.edges:
        executor._edge_index.setdefault(edge.source, []).append(edge)
    return executor


class TestParseFnVerdictFeedback:
    """Test 8: executor _parse_fn_verdict passes through reloop text."""

    def test_reloop_feedback_passthrough(self) -> None:
        executor = _make_executor()
        verdict = executor._parse_fn_verdict(
            "reloop: compilation failed — error on line 42\n", "gate_verify"
        )
        assert verdict.type == VerdictType.RELOOP
        assert verdict.feedback == "compilation failed — error on line 42"

    def test_reloop_no_text_fallback(self) -> None:
        executor = _make_executor()
        verdict = executor._parse_fn_verdict("reloop:\n", "gate_verify")
        assert verdict.type == VerdictType.RELOOP
        assert verdict.feedback == "fn gate requested reloop"

    def test_reloop_bare_word_fallback(self) -> None:
        executor = _make_executor()
        verdict = executor._parse_fn_verdict("reloop\n", "gate_verify")
        assert verdict.type == VerdictType.RELOOP
        assert verdict.feedback == "fn gate requested reloop"
