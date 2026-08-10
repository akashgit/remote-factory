"""Tests for factory.optimization.benchmarks.harbor — HarborBenchmark executor."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import patch as mock_patch

from factory.optimization.benchmarks.harbor import HarborBenchmark
from factory.optimization.protocols import Executor
from factory.optimization.surface import Surface


class TestHarborBenchmarkConstruction:
    def test_default_params(self) -> None:
        hb = HarborBenchmark()
        assert hb.git_ref == "main"
        assert hb.concurrency == 5
        assert hb.model == "sonnet"
        assert hb.cleanup_jobs is True
        assert hb.subset_dir is None

    def test_custom_params(self) -> None:
        hb = HarborBenchmark(
            git_ref="feat/test",
            subset_dir="/tmp/subset",
            concurrency=10,
            docker_host="unix:///run/podman.sock",
            model="opus",
            auth_env={"ANTHROPIC_VERTEX_PROJECT_ID": "my-project"},
            cleanup_jobs=False,
        )
        assert hb.git_ref == "feat/test"
        assert hb.subset_dir == Path("/tmp/subset")
        assert hb.concurrency == 10
        assert hb.docker_host == "unix:///run/podman.sock"
        assert hb.model == "opus"
        assert hb.auth_env == {"ANTHROPIC_VERTEX_PROJECT_ID": "my-project"}
        assert hb.cleanup_jobs is False

    def test_protocol_conformance(self) -> None:
        assert isinstance(HarborBenchmark(), Executor)


class TestHarborBenchmarkExecute:
    def test_command_includes_uvx_harbor_run(self, tmp_path: Path) -> None:
        hb = HarborBenchmark(git_ref="main", concurrency=5, cleanup_jobs=False)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, Surface())

        assert captured_cmd[0] == "uvx"
        assert captured_cmd[1] == "harbor"
        assert captured_cmd[2] == "run"

    def test_skill_b64_in_ae_flag(self, tmp_path: Path) -> None:
        hb = HarborBenchmark(cleanup_jobs=False)
        skill_content = "You are a search assistant."
        surface = Surface(prompt_slots={"skill": skill_content})
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, surface)

        expected_b64 = base64.b64encode(skill_content.encode()).decode()
        ae_indices = [i for i, v in enumerate(captured_cmd) if v == "--ae"]
        ae_values = [captured_cmd[i + 1] for i in ae_indices]
        skill_ae = [v for v in ae_values if v.startswith("SEARCHQA_SKILL_B64=")]
        assert len(skill_ae) == 1
        assert skill_ae[0] == f"SEARCHQA_SKILL_B64={expected_b64}"

    def test_auth_env_propagation(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "test-proj-123")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-central1")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

        hb = HarborBenchmark(cleanup_jobs=False)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, Surface())

        ae_indices = [i for i, v in enumerate(captured_cmd) if v == "--ae"]
        ae_values = [captured_cmd[i + 1] for i in ae_indices]
        assert "CLAUDE_CODE_USE_VERTEX=1" in ae_values
        assert "ANTHROPIC_VERTEX_PROJECT_ID=test-proj-123" in ae_values
        assert "CLOUD_ML_REGION=us-central1" in ae_values

    def test_concurrency_and_model_in_command(self, tmp_path: Path) -> None:
        hb = HarborBenchmark(concurrency=8, model="opus-4-6", cleanup_jobs=False)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, Surface())

        assert "--n-concurrent" in captured_cmd
        assert captured_cmd[captured_cmd.index("--n-concurrent") + 1] == "8"
        assert "--model" in captured_cmd
        assert captured_cmd[captured_cmd.index("--model") + 1] == "anthropic/claude-opus-4-6"

    def test_short_model_name_gets_prefix(self, tmp_path: Path) -> None:
        hb = HarborBenchmark(model="sonnet", cleanup_jobs=False)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, Surface())

        assert captured_cmd[captured_cmd.index("--model") + 1] == "anthropic/claude-sonnet"


class TestHarborBenchmarkResultParsing:
    def test_parses_trial_results(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        for tid, reward_val in [("task1__abc1234", 1.0), ("task2__def5678", 0.0)]:
            trial = jobs_dir / tid
            verifier = trial / "verifier"
            verifier.mkdir(parents=True)
            verifier.joinpath("reward.json").write_text(json.dumps({"reward": reward_val}))
            verifier.joinpath("test-stdout.txt").write_text(
                f"Predicted: answer_{tid[:5]}\nGold: gold_{tid[:5]}\n"
            )

        hb = HarborBenchmark(cleanup_jobs=False)

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(jobs_dir)):
            result = hb.execute(tmp_path, Surface())

        assert len(result.task_results) == 2
        ids = {t.task_id for t in result.task_results}
        assert "task1" in ids
        assert "task2" in ids

        correct = sum(1 for t in result.task_results if t.reward > 0)
        assert correct == 1

    def test_accuracy_artifact_created(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        trial = jobs_dir / "t1__abc1234"
        verifier = trial / "verifier"
        verifier.mkdir(parents=True)
        verifier.joinpath("reward.json").write_text(json.dumps({"reward": 1.0}))

        hb = HarborBenchmark(cleanup_jobs=False)

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(jobs_dir)):
            result = hb.execute(tmp_path, Surface())

        assert len(result.artifacts) == 1
        agg = json.loads(Path(result.artifacts[0]).read_text())
        assert agg["accuracy"] == 1.0


class TestHarborBenchmarkCleanup:
    def test_cleanup_removes_jobs_dir(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "somefile").write_text("data")

        hb = HarborBenchmark(cleanup_jobs=True)

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(jobs_dir)):
            hb.execute(tmp_path, Surface())

        assert not jobs_dir.exists()

    def test_no_cleanup_preserves_jobs_dir(self, tmp_path: Path) -> None:
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "somefile").write_text("data")

        hb = HarborBenchmark(cleanup_jobs=False)

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(jobs_dir)):
            hb.execute(tmp_path, Surface())

        assert jobs_dir.exists()


class TestHarborBenchmarkSubsetDir:
    def test_subset_dir_used_in_command(self, tmp_path: Path) -> None:
        subset = tmp_path / "subset"
        subset.mkdir()
        hb = HarborBenchmark(subset_dir=str(subset), cleanup_jobs=False)
        captured_cmd: list[str] = []

        def mock_run(cmd, cwd=None, env=None, capture_output=False, text=False):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock_patch("factory.optimization.benchmarks.harbor.subprocess.run", mock_run), \
             mock_patch("factory.optimization.benchmarks.harbor.tempfile.mkdtemp", return_value=str(tmp_path / "jobs")):
            (tmp_path / "jobs").mkdir()
            hb.execute(tmp_path, Surface())

        p_idx = captured_cmd.index("-p")
        assert captured_cmd[p_idx + 1] == str(subset)
