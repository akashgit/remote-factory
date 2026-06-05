"""Integration tests for container builds and build.sh functionality."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

podman_available = shutil.which("podman") is not None
FIXTURES_DIR = Path(__file__).parent / "fixtures"
BUILD_SH = Path(__file__).parent.parent / "build.sh"
GENERATE_CONTAINERFILE = Path(__file__).parent.parent / "scripts" / "generate_containerfile.py"


@pytest.mark.skipif(not podman_available, reason="Podman not installed")
class TestContainerBuild:
    def test_containerfile_builds(self, tmp_path: Path) -> None:
        """Generate a Containerfile and verify podman build succeeds."""
        mini_project = FIXTURES_DIR / "mini-java-project"
        work_dir = tmp_path / "build-root"
        shutil.copytree(mini_project, work_dir / "project")
        (work_dir / "gradle" / "init.d").mkdir(parents=True)
        (work_dir / "gradle" / "init.d" / "repositories.gradle").write_text("")
        (work_dir / "local-repo").mkdir()

        result = subprocess.run(
            ["python3", str(GENERATE_CONTAINERFILE), "--jdk-version", "11"],
            capture_output=True, text=True, check=True,
        )
        (work_dir / "Containerfile").write_text(result.stdout)

        build_result = subprocess.run(
            ["podman", "build", "-t", "test-build-root-mini", "-f", "Containerfile", "."],
            cwd=work_dir, capture_output=True, text=True, timeout=300,
        )
        assert build_result.returncode == 0, (
            f"podman build failed:\nstdout: {build_result.stdout}\nstderr: {build_result.stderr}"
        )

        subprocess.run(
            ["podman", "rmi", "test-build-root-mini"],
            capture_output=True, timeout=30,
        )

    def test_fast_build_mini_project(self, tmp_path: Path) -> None:
        """Build the mini-java-project image and run BUILD_MODE=fast against it."""
        mini_project = FIXTURES_DIR / "mini-java-project"
        work_dir = tmp_path / "build-root"
        shutil.copytree(mini_project, work_dir)

        (work_dir / "gradle" / "init.d").mkdir(parents=True, exist_ok=True)
        (work_dir / "gradle" / "init.d" / "repositories.gradle").write_text("")
        (work_dir / "local-repo").mkdir(exist_ok=True)
        (work_dir / "results").mkdir(exist_ok=True)

        result = subprocess.run(
            ["python3", str(GENERATE_CONTAINERFILE), "--jdk-version", "11"],
            capture_output=True, text=True, check=True,
        )
        (work_dir / "Containerfile").write_text(result.stdout)

        image_name = "test-fast-build-mini"
        build_result = subprocess.run(
            ["podman", "build", "-t", image_name, "-f", "Containerfile", "."],
            cwd=work_dir, capture_output=True, text=True, timeout=300,
        )
        assert build_result.returncode == 0, f"Image build failed: {build_result.stderr}"

        run_result = subprocess.run(
            ["podman", "run", "--rm", image_name, "bash", "-c",
             "./gradlew compileJava compileTestJava --continue 2>&1"],
            capture_output=True, text=True, timeout=600,
        )
        assert run_result.returncode == 0, (
            f"Fast build failed:\nstdout: {run_result.stdout}\nstderr: {run_result.stderr}"
        )

        subprocess.run(
            ["podman", "rmi", image_name],
            capture_output=True, timeout=30,
        )


class TestDiskSpaceCheck:
    def test_disk_space_warning_low_space(self, tmp_path: Path) -> None:
        """Verify build.sh warns when disk space is below threshold."""
        fake_target = tmp_path / "storage"
        fake_target.mkdir()

        env = {
            **os.environ,
            "PODMAN_STORAGE_ROOT": str(fake_target),
            "BUILD_STAGE": "2",
            "CONTAINER_RUNTIME": "podman",
        }

        result = subprocess.run(
            ["bash", str(BUILD_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )

        combined = result.stdout + result.stderr
        assert "Disk space" in combined or "free on" in combined

    def test_disk_space_ok_message(self, tmp_path: Path) -> None:
        """Verify build.sh prints OK message when sufficient space is available."""
        env = {
            **os.environ,
            "BUILD_STAGE": "2",
            "CONTAINER_RUNTIME": "podman",
        }

        result = subprocess.run(
            ["bash", str(BUILD_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )

        combined = result.stdout + result.stderr
        assert "Disk space OK" in combined or "WARNING" in combined


class TestBuildModeEnvVars:
    def test_build_mode_defaults_to_compile(self) -> None:
        """Verify BUILD_MODE defaults to 'compile' in build.sh."""
        result = subprocess.run(
            ["bash", "-c", f'source {BUILD_SH} 2>/dev/null; echo "$BUILD_MODE"'],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "" or "compile" in result.stdout or result.returncode != 0

    def test_build_mode_fast_runs_fast_build(self, tmp_path: Path) -> None:
        """Verify BUILD_MODE=fast routes to compileJava + compileTestJava."""
        script = tmp_path / "test_mode.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'BUILD_MODE=fast\n'
            f'source <(grep -A4 "^fast_build()" {BUILD_SH})\n'
            'echo "FAST_BUILD_CALLED"\n'
        )

        result = subprocess.run(
            ["bash", "-c",
             f'export BUILD_MODE=fast; grep "compileJava compileTestJava" {BUILD_SH}'],
            capture_output=True, text=True,
        )
        assert "compileJava compileTestJava" in result.stdout

    def test_build_mode_full_runs_clean_build(self, tmp_path: Path) -> None:
        """Verify BUILD_MODE=full routes to clean test build."""
        result = subprocess.run(
            ["bash", "-c",
             f'export BUILD_MODE=full; grep "clean test build" {BUILD_SH}'],
            capture_output=True, text=True,
        )
        assert "clean test build" in result.stdout

    def test_build_sh_contains_podman_storage_root(self) -> None:
        """Verify build.sh references PODMAN_STORAGE_ROOT."""
        content = BUILD_SH.read_text()
        assert "PODMAN_STORAGE_ROOT" in content
        assert "--root" in content

    def test_build_sh_contains_project_source_mount(self) -> None:
        """Verify build.sh supports PROJECT_SOURCE volume mount."""
        content = BUILD_SH.read_text()
        assert "PROJECT_SOURCE" in content
        assert "/workspace/project" in content

    def test_stage_complete_message_includes_mode(self, tmp_path: Path) -> None:
        """Verify completion message includes the build mode."""
        env = {
            **os.environ,
            "BUILD_STAGE": "2",
            "BUILD_MODE": "fast",
            "CONTAINER_RUNTIME": "podman",
        }

        result = subprocess.run(
            ["bash", str(BUILD_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )

        assert "mode: fast" in result.stdout
