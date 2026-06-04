"""Universal hygiene eval dimensions applied to every factory-managed project.

These 6 dimensions are mandatory and cannot be removed. They are computed by
the factory itself (not by per-project eval/score.py) and auto-detect the
project's tooling. Projects can ADD dimensions via eval/score.py but cannot
remove any of these.

Together with the 5 growth dimensions in growth.py, these form the 11
mandatory eval dimensions that define the factory's quality baseline.

All functions take a project_path and return an EvalResult-compatible dict.
If a tool is not detected for a dimension, score is 0.5 (neutral), not 0.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import structlog

log = structlog.get_logger()

# Relative weights within the hygiene category (sum to 1.0).
# The runner normalizes these so that hygiene gets 50% of the composite.
HYGIENE_WEIGHTS = {
    "tests": 0.30,
    "lint": 0.15,
    "type_check": 0.10,
    "coverage": 0.25,
    "guard_patterns": 0.10,
    "config_parser": 0.10,
}


# ── Tool detection ─────────────────────────────────────────────────


def _find_sub_projects(project_path: Path) -> list[Path]:
    """Find project roots (dirs with pyproject.toml, package.json, Cargo.toml, go.mod).

    Checks the project root and immediate subdirectories. Returns the project
    root itself if it has project markers, plus any sub-project dirs.
    """
    markers = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts"]
    skip = {".git", ".factory", "node_modules", ".venv", "venv", "__pycache__"}
    roots: list[Path] = []

    # Check top level
    if any((project_path / m).exists() for m in markers):
        roots.append(project_path)

    # Check immediate subdirs
    for child in sorted(project_path.iterdir()):
        if not child.is_dir() or child.name in skip or child.name.startswith("."):
            continue
        # Also follow symlinks
        resolved = child.resolve()
        if any((resolved / m).exists() for m in markers):
            roots.append(child)

    # Rust workspace dedup: if a root has a workspace Cargo.toml, remove member sub-crates
    workspace_roots: list[Path] = []
    for r in roots:
        cargo = r / "Cargo.toml"
        if cargo.exists():
            try:
                if "[workspace]" in cargo.read_text():
                    workspace_roots.append(r.resolve())
            except OSError as exc:
                log.debug("cargo_toml_read_failed", path=str(cargo), exc=str(exc))
    if workspace_roots:
        roots = [
            r for r in roots
            if not any(
                r.resolve() != ws and str(r.resolve()).startswith(str(ws) + os.sep)
                for ws in workspace_roots
            )
        ]

    return roots or [project_path]


def _detect_python_project(project_path: Path) -> bool:
    return (project_path / "pyproject.toml").exists() or (project_path / "setup.py").exists()


def _detect_node_project(project_path: Path) -> bool:
    return (project_path / "package.json").exists()


def _detect_rust_project(project_path: Path) -> bool:
    return (project_path / "Cargo.toml").exists()


def _detect_go_project(project_path: Path) -> bool:
    return (project_path / "go.mod").exists()


def _detect_java_project(project_path: Path) -> bool:
    return (
        (project_path / "pom.xml").exists()
        or (project_path / "build.gradle").exists()
        or (project_path / "build.gradle.kts").exists()
    )


def _java_build_tool(project_path: Path) -> list[str] | None:
    """Return the Java build tool command prefix, or None if not available."""
    gradlew = project_path / "gradlew"
    if gradlew.exists():
        return [str(gradlew)]
    if shutil.which("gradle") and (
        (project_path / "build.gradle").exists() or (project_path / "build.gradle.kts").exists()
    ):
        return ["gradle"]
    if shutil.which("mvn") and (project_path / "pom.xml").exists():
        return ["mvn"]
    return None


def _java_test_cmd(project_path: Path) -> list[str] | None:
    tool = _java_build_tool(project_path)
    if not tool:
        return None
    return [*tool, "test", "-q"]


def _run_cmd(
    cmd: list[str],
    cwd: Path,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir() and str(cargo_bin) not in env.get("PATH", ""):
        env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        if result.returncode != 0:
            log.debug(
                "subprocess_failed",
                cmd=cmd,
                cwd=str(cwd),
                returncode=result.returncode,
                stderr=result.stderr[:200] if result.stderr else "",
            )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, "", str(exc)


def _neutral(name: str, reason: str) -> dict:
    """Return a neutral score (0.5) when a tool isn't detected."""
    return {
        "name": name,
        "score": 0.5,
        "weight": HYGIENE_WEIGHTS[name],
        "passed": True,
        "details": f"Not detected: {reason}",
    }


# ── Dimension 1: tests (weight 0.30) ──────────────────────────────


def eval_tests(project_path: Path) -> dict:
    """Run test suites across all detected sub-projects. Parse pass/fail ratio."""
    sub_projects = _find_sub_projects(project_path)
    total_passed = 0
    total_failed = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            # Try pytest
            rc, stdout, stderr = _run_cmd([sys.executable, "-m", "pytest", "-v", "--tb=no", "-q"], sp)
            output = stdout + stderr
            p_match = re.search(r"(\d+)\s+passed", output)
            f_match = re.search(r"(\d+)\s+failed", output)
            p = int(p_match.group(1)) if p_match else 0
            f = int(f_match.group(1)) if f_match else 0
            if p + f > 0:
                ran_any = True
                total_passed += p
                total_failed += f
                details_parts.append(f"{sp.name}: {p} passed, {f} failed")

        if _detect_node_project(sp):
            # Try npm test
            rc, stdout, stderr = _run_cmd(["npm", "test", "--", "--passWithNoTests"], sp, timeout=180)
            output = stdout + stderr
            # Jest: match only "Tests:" lines, not "Test Suites:" lines
            p_matches = re.findall(r"^Tests:.*?(\d+)\s+passed", output, re.MULTILINE)
            f_matches = re.findall(r"^Tests:.*?(\d+)\s+failed", output, re.MULTILINE)
            p = sum(int(x) for x in p_matches)
            f = sum(int(x) for x in f_matches)
            if p + f > 0:
                ran_any = True
                total_passed += p
                total_failed += f
                details_parts.append(f"{sp.name}(js): {p} passed, {f} failed")

        if _detect_rust_project(sp):
            if not shutil.which("cargo"):
                log.warning("cargo_not_found", project=str(sp), msg="cargo not on PATH, skipping Rust tests")
            else:
                rc, stdout, stderr = _run_cmd(["cargo", "test", "--workspace"], sp)
                output = stdout + stderr
                p_matches = re.findall(r"(\d+)\s+passed", output)
                f_matches = re.findall(r"(\d+)\s+failed", output)
                p = sum(int(x) for x in p_matches)
                f = sum(int(x) for x in f_matches)
                if p + f > 0:
                    ran_any = True
                    total_passed += p
                    total_failed += f
                    details_parts.append(f"{sp.name}(rs): {p} passed, {f} failed")

        if _detect_go_project(sp):
            if not shutil.which("go"):
                log.warning("go_not_found", project=str(sp), msg="go not on PATH, skipping Go tests")
            else:
                rc, stdout, stderr = _run_cmd(["go", "test", "./..."], sp)
                output = stdout + stderr
                if rc == 0:
                    ran_any = True
                    ok_count = len(re.findall(r"^ok\s+", output, re.MULTILINE))
                    total_passed += max(ok_count, 1)
                    details_parts.append(f"{sp.name}(go): passed")
                elif "FAIL" in output:
                    ran_any = True
                    total_failed += 1
                    details_parts.append(f"{sp.name}(go): failed")

        if _detect_java_project(sp):
            java_cmd = _java_test_cmd(sp)
            if not java_cmd:
                log.warning("java_build_tool_not_found", project=str(sp), msg="mvn/gradle not on PATH, skipping Java tests")
            else:
                rc, stdout, stderr = _run_cmd(java_cmd, sp)
                output = stdout + stderr
                t_matches = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", output)
                if t_matches:
                    ran_any = True
                    java_passed = 0
                    java_failed = 0
                    for total_run_s, fail_s, err_s in t_matches:
                        p = int(total_run_s) - int(fail_s) - int(err_s)
                        java_passed += p
                        java_failed += int(fail_s) + int(err_s)
                    total_passed += java_passed
                    total_failed += java_failed
                    details_parts.append(f"{sp.name}(java): {java_passed} passed, {java_failed} failed")
                elif rc == 0:
                    log.warning("java_tests_unparsed", project=str(sp), msg="Tests passed but output format unrecognized")

    if not ran_any:
        return _neutral("tests", "no test suite detected")

    total = total_passed + total_failed
    score = total_passed / total if total > 0 else 0.0
    return {
        "name": "tests",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["tests"],
        "passed": total_failed == 0,
        "details": "; ".join(details_parts) or f"{total_passed} passed, {total_failed} failed",
    }


# ── Dimension 2: lint (weight 0.15) ───────────────────────────────


def eval_lint(project_path: Path) -> dict:
    """Run linters across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    total_errors = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            rc, stdout, stderr = _run_cmd([sys.executable, "-m", "ruff", "check", "."], sp)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}: clean")
            else:
                ran_any = True
                err_match = re.search(r"Found\s+(\d+)\s+error", output)
                count = int(err_match.group(1)) if err_match else 1
                total_errors += count
                details_parts.append(f"{sp.name}: {count} errors")

        if _detect_node_project(sp):
            rc, stdout, stderr = _run_cmd(["npx", "eslint", ".", "--format=compact"], sp, timeout=180)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}(js): clean")
            else:
                ran_any = True
                count = len(re.findall(r"Error -", output))
                total_errors += max(count, 1)
                details_parts.append(f"{sp.name}(js): {max(count, 1)} errors")

        if _detect_rust_project(sp):
            if not shutil.which("cargo"):
                log.warning("cargo_not_found", project=str(sp), msg="cargo not on PATH, skipping Rust lint")
            else:
                rc, stdout, stderr = _run_cmd(["cargo", "clippy", "--", "-D", "warnings"], sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(rs): clean")
                else:
                    ran_any = True
                    count = len(re.findall(r"^error", stderr, re.MULTILINE))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(rs): {max(count, 1)} errors")

        if _detect_go_project(sp):
            if not shutil.which("go"):
                log.warning("go_not_found", project=str(sp), msg="go not on PATH, skipping Go lint")
            else:
                rc, stdout, stderr = _run_cmd(["go", "vet", "./..."], sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(go): clean")
                else:
                    ran_any = True
                    output = stdout + stderr
                    count = len(re.findall(r"^.*\.go:\d+:\d+:", output, re.MULTILINE))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(go): {max(count, 1)} errors")

        if _detect_java_project(sp):
            tool = _java_build_tool(sp)
            if not tool:
                log.warning("java_build_tool_not_found", project=str(sp), msg="mvn/gradle not on PATH, skipping Java lint")
            else:
                if tool[-1] == "mvn":
                    cmd = [*tool, "checkstyle:check", "-q"]
                else:
                    cmd = [*tool, "checkstyleMain", "-q"]
                rc, stdout, stderr = _run_cmd(cmd, sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(java): clean")
                else:
                    ran_any = True
                    output = stdout + stderr
                    count = len(re.findall(r"\[ERROR\]", output))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(java): {max(count, 1)} errors")

    if not ran_any:
        return _neutral("lint", "no linter detected")

    score = max(0.0, 1.0 - total_errors * 0.1)
    return {
        "name": "lint",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["lint"],
        "passed": total_errors == 0,
        "details": "; ".join(details_parts),
    }


# ── Dimension 3: type_check (weight 0.10) ─────────────────────────


def eval_type_check(project_path: Path) -> dict:
    """Run type checkers across detected sub-projects. Partial credit per error."""
    sub_projects = _find_sub_projects(project_path)
    total_errors = 0
    ran_any = False
    details_parts: list[str] = []

    for sp in sub_projects:
        if _detect_python_project(sp):
            # Find the main source dir (first dir with __init__.py)
            src_dirs = []
            for child in sorted(sp.iterdir()):
                if child.is_dir() and (child / "__init__.py").exists():
                    src_dirs.append(child.name)
            target = src_dirs[0] if src_dirs else "."
            rc, stdout, stderr = _run_cmd([sys.executable, "-m", "mypy", target], sp)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}: clean")
            else:
                ran_any = True
                err_match = re.search(r"Found\s+(\d+)\s+error", output)
                count = int(err_match.group(1)) if err_match else 1
                total_errors += count
                details_parts.append(f"{sp.name}: {count} errors")

        if _detect_node_project(sp):
            rc, stdout, stderr = _run_cmd(["npx", "tsc", "--noEmit"], sp, timeout=180)
            output = stdout + stderr
            if rc == 0:
                ran_any = True
                details_parts.append(f"{sp.name}(ts): clean")
            else:
                ran_any = True
                count = len(re.findall(r"error TS\d+", output))
                total_errors += max(count, 1)
                details_parts.append(f"{sp.name}(ts): {max(count, 1)} errors")

        if _detect_rust_project(sp):
            if not shutil.which("cargo"):
                log.warning("cargo_not_found", project=str(sp), msg="cargo not on PATH, skipping Rust type check")
            else:
                rc, stdout, stderr = _run_cmd(["cargo", "check"], sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(rs): clean")
                else:
                    ran_any = True
                    count = len(re.findall(r"^error", stderr, re.MULTILINE))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(rs): {max(count, 1)} errors")

        if _detect_go_project(sp):
            if not shutil.which("go"):
                log.warning("go_not_found", project=str(sp), msg="go not on PATH, skipping Go type check")
            else:
                rc, stdout, stderr = _run_cmd(["go", "build", "-o", os.devnull, "./..."], sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(go): clean")
                else:
                    ran_any = True
                    output = stdout + stderr
                    count = len(re.findall(r"^.*\.go:\d+:\d+:", output, re.MULTILINE))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(go): {max(count, 1)} errors")

        if _detect_java_project(sp):
            tool = _java_build_tool(sp)
            if not tool:
                log.warning("java_build_tool_not_found", project=str(sp), msg="mvn/gradle not on PATH, skipping Java type check")
            else:
                if tool[-1] == "mvn":
                    cmd = [*tool, "compile", "-q"]
                else:
                    cmd = [*tool, "compileJava", "-q"]
                rc, stdout, stderr = _run_cmd(cmd, sp)
                if rc == 0:
                    ran_any = True
                    details_parts.append(f"{sp.name}(java): clean")
                else:
                    ran_any = True
                    output = stdout + stderr
                    count = len(re.findall(r"\[ERROR\]", output))
                    total_errors += max(count, 1)
                    details_parts.append(f"{sp.name}(java): {max(count, 1)} errors")

    if not ran_any:
        return _neutral("type_check", "no type checker detected")

    score = max(0.0, 1.0 - total_errors * 0.05)
    return {
        "name": "type_check",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["type_check"],
        "passed": total_errors == 0,
        "details": "; ".join(details_parts),
    }


# ── Dimension 4: coverage (weight 0.25) ───────────────────────────


def eval_coverage(project_path: Path) -> dict:
    """Run test coverage across detected sub-projects."""
    sub_projects = _find_sub_projects(project_path)
    coverages: list[tuple[str, int]] = []
    ran_any = False

    for sp in sub_projects:
        if _detect_python_project(sp):
            src_dirs = [
                c.name for c in sorted(sp.iterdir())
                if c.is_dir() and (c / "__init__.py").exists()
            ]
            cov_target = src_dirs[0] if src_dirs else "."
            rc, stdout, stderr = _run_cmd(
                [sys.executable, "-m", "pytest", f"--cov={cov_target}", "--cov-report=term", "-q"],
                sp,
            )
            output = stdout + stderr
            total_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if total_match:
                ran_any = True
                pct = int(total_match.group(1))
                coverages.append((sp.name, pct))

        if _detect_rust_project(sp):
            if not shutil.which("cargo"):
                log.warning("cargo_not_found", project=str(sp), msg="cargo not on PATH, skipping Rust coverage")
            else:
                rc, stdout, stderr = _run_cmd(
                    ["cargo", "llvm-cov", "--summary-only"],
                    sp,
                    timeout=600,
                )
                output = stdout + stderr
                pct_match = None
                if rc == 0:
                    pct_match = re.search(r"TOTAL\s+[\d.]+\s+[\d.]+\s+([\d.]+)%", output)
                else:
                    llvm_cov_stderr = stderr
                    rc, stdout, stderr = _run_cmd(
                        ["cargo", "tarpaulin", "--out", "stdout", "--skip-clean"],
                        sp,
                        timeout=600,
                    )
                    output = stdout + stderr
                    pct_match = re.search(r"(\d+(?:\.\d+)?)%\s+coverage", output)
                    if not pct_match:
                        stderr = llvm_cov_stderr
                if pct_match:
                    ran_any = True
                    pct = int(float(pct_match.group(1)))
                    coverages.append((f"{sp.name}(rs)", pct))
                elif "Timed out" in stderr:
                    log.warning("coverage_timeout", project=str(sp), lang="rust", timeout=600)
                elif rc != 0:
                    log.warning("coverage_tool_failed", project=str(sp), lang="rust", rc=rc, stderr=stderr[:200])

        if _detect_go_project(sp):
            if not shutil.which("go"):
                log.warning("go_not_found", project=str(sp), msg="go not on PATH, skipping Go coverage")
            else:
                rc, stdout, stderr = _run_cmd(
                    ["go", "test", "-cover", "./..."],
                    sp,
                )
                output = stdout + stderr
                pcts = [float(m) for m in re.findall(r"coverage:\s+(\d+(?:\.\d+)?)%", output)]
                if pcts:
                    ran_any = True
                    avg = int(sum(pcts) / len(pcts))
                    coverages.append((f"{sp.name}(go)", avg))
                else:
                    log.warning("coverage_output_unrecognized", project=str(sp), lang="go")

        if _detect_node_project(sp):
            if not shutil.which("npx"):
                log.warning("npx_not_found", project=str(sp), msg="npx not on PATH, skipping Node coverage")
            else:
                rc, stdout, stderr = _run_cmd(
                    ["npx", "--no-install", "jest", "--coverage", "--coverageReporters=text", "--passWithNoTests"],
                    sp,
                    timeout=180,
                )
                output = stdout + stderr
                pct_match = re.search(r"All files\s*\|\s*(\d+(?:\.\d+)?)", output)
                if pct_match:
                    ran_any = True
                    pct = int(float(pct_match.group(1)))
                    coverages.append((f"{sp.name}(js)", pct))
                else:
                    log.warning("coverage_output_unrecognized", project=str(sp), lang="node")

        if _detect_java_project(sp):
            tool = _java_build_tool(sp)
            if not tool:
                log.warning("java_build_tool_not_found", project=str(sp), msg="mvn/gradle not on PATH, skipping Java coverage")
            else:
                if tool[-1] == "mvn":
                    cmd = [*tool, "verify", "-q", "-Djacoco.skip=false"]
                    jacoco_xml = sp / "target" / "site" / "jacoco" / "jacoco.xml"
                else:
                    cmd = [*tool, "jacocoTestReport", "-q"]
                    jacoco_xml = sp / "build" / "reports" / "jacoco" / "test" / "jacocoTestReport.xml"
                rc, stdout, stderr = _run_cmd(cmd, sp)
                if rc == 0 and jacoco_xml.exists():
                    try:
                        xml_text = jacoco_xml.read_text()
                        line_counter = re.search(r'<counter[^>]+type="LINE"[^>]*/>', xml_text)
                        if line_counter:
                            missed_m = re.search(r'missed="(\d+)"', line_counter.group(0))
                            covered_m = re.search(r'covered="(\d+)"', line_counter.group(0))
                        else:
                            missed_m = covered_m = None
                        if missed_m and covered_m:
                            missed = int(missed_m.group(1))
                            covered = int(covered_m.group(1))
                            total_lines = missed + covered
                            pct = int(covered * 100 / total_lines) if total_lines > 0 else 0
                            ran_any = True
                            coverages.append((f"{sp.name}(java)", pct))
                        else:
                            log.warning("jacoco_xml_no_line_counter", project=str(sp))
                    except OSError:
                        log.warning("jacoco_xml_read_failed", project=str(sp))
                elif rc == 0:
                    log.warning("jacoco_xml_not_found", project=str(sp), path=str(jacoco_xml))

    if not ran_any:
        return _neutral("coverage", "no coverage tool detected")

    avg_pct = sum(p for _, p in coverages) / len(coverages)
    score = avg_pct / 100.0
    details = ", ".join(f"{name}: {pct}%" for name, pct in coverages)
    return {
        "name": "coverage",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["coverage"],
        "passed": avg_pct >= 80,
        "details": f"Coverage: {details} (threshold: 80%)",
    }


# ── Dimension 5: guard_patterns (weight 0.10) ─────────────────────


def eval_guard_patterns(project_path: Path) -> dict:
    """Test that the factory's guard glob matching works correctly on this project."""
    try:
        from factory.eval.guards import _glob_match
    except (ImportError, AttributeError) as exc:
        return {
            "name": "guard_patterns",
            "score": 0.0,
            "weight": HYGIENE_WEIGHTS["guard_patterns"],
            "passed": False,
            "details": f"Could not import _glob_match: {exc}",
        }

    # Read project scope from config if available
    scope_patterns: list[str] = []
    config_path = project_path / ".factory" / "config.json"
    if config_path.exists():
        import json
        try:
            data = json.loads(config_path.read_text())
            scope_patterns = data.get("scope", [])
        except (json.JSONDecodeError, KeyError):
            pass

    # Build test cases from the project's actual scope + universal cases
    test_cases: list[tuple[str, str, bool]] = [
        # Universal: .factory/ should never match user scope
        ("src/**/*.py", ".factory/config.json", False),
        ("src/**/*.py", "src/main.py", True),
        ("tests/**/*.py", "tests/test_main.py", True),
        ("tests/**/*.py", "src/main.py", False),
    ]

    # Add project-specific scope tests
    for pattern in scope_patterns[:4]:
        # The pattern itself should match something reasonable
        if "**" in pattern:
            parts = pattern.split("**")
            prefix = parts[0].rstrip("/")
            if prefix:
                test_cases.append((pattern, f"{prefix}/example.py", True))
                test_cases.append((pattern, "unrelated/file.txt", False))

    correct = 0
    details: list[str] = []
    for pattern, filepath, expected in test_cases:
        actual = _glob_match(filepath, pattern)
        if actual == expected:
            correct += 1
        else:
            details.append(f"FAIL: {pattern} vs {filepath} expected={expected} got={actual}")

    total = len(test_cases)
    score = correct / total if total > 0 else 1.0
    summary = f"{correct}/{total} pattern tests passed"
    if details:
        summary += "; " + "; ".join(details[:3])

    return {
        "name": "guard_patterns",
        "score": round(score, 4),
        "weight": HYGIENE_WEIGHTS["guard_patterns"],
        "passed": correct == total,
        "details": summary,
    }


# ── Dimension 6: config_parser (weight 0.10) ──────────────────────


def _parse_factory_md(path: Path) -> dict[str, str | list[str] | float]:
    """Synchronously parse factory.md into a dict of config fields.

    Replicates the parsing logic from ExperimentStore.reparse_config()
    without requiring asyncio, so it can be called safely from sync code
    that may already be running inside an async event loop.
    """
    text = path.read_text()
    parsed: dict[str, str | list[str] | float] = {}
    current_section: str | None = None
    list_buffer: list[str] = []
    in_code_block = False

    section_map: dict[str, str] = {
        "command": "eval_command",
        "threshold": "eval_threshold",
        "modifiable": "scope",
        "read_only": "read_only",
    }

    def _flush_list() -> None:
        if current_section and list_buffer:
            parsed[current_section] = list(list_buffer)
            list_buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            if stripped and current_section:
                parsed[current_section] = stripped
            continue

        if stripped.startswith("#"):
            _flush_list()
            heading = stripped.lstrip("#").strip().lower().replace(" ", "_")
            mapped = section_map.get(heading, heading)
            current_section = mapped
        elif stripped.startswith("- ") and current_section:
            list_buffer.append(stripped[2:].strip())
        elif stripped and current_section and not list_buffer:
            if current_section == "eval_threshold":
                parsed[current_section] = float(stripped)
            else:
                parsed[current_section] = stripped
    _flush_list()

    return parsed


def eval_config_parser(project_path: Path) -> dict:
    """Test that factory.md can be parsed and essential fields extracted."""
    factory_md = project_path / "factory.md"
    if not factory_md.exists():
        return _neutral("config_parser", "no factory.md found")

    try:
        parsed = _parse_factory_md(factory_md)

        goal = parsed.get("goal", "")
        scope = parsed.get("scope", [])
        eval_command = parsed.get("eval_command", "")
        eval_threshold = parsed.get("eval_threshold", 0.0)

        checks: list[tuple[str, bool]] = [
            ("goal is non-empty", bool(goal and len(str(goal)) > 0)),
            ("scope has entries", isinstance(scope, list) and len(scope) > 0),
            ("eval_command is non-empty", bool(eval_command)),
            ("eval_threshold is positive", float(str(eval_threshold)) > 0),
        ]

        correct = sum(1 for _, ok in checks if ok)
        total = len(checks)
        score = correct / total
        detail_parts = [f"{'OK' if ok else 'FAIL'}: {label}" for label, ok in checks]

        return {
            "name": "config_parser",
            "score": round(score, 4),
            "weight": HYGIENE_WEIGHTS["config_parser"],
            "passed": correct == total,
            "details": "; ".join(detail_parts),
        }
    except Exception as exc:
        return {
            "name": "config_parser",
            "score": 0.0,
            "weight": HYGIENE_WEIGHTS["config_parser"],
            "passed": False,
            "details": f"Error: {exc}",
        }


# ── Public API ─────────────────────────────────────────────────────


def compute_hygiene_results(project_path: Path) -> list[dict]:
    """Compute all 6 mandatory hygiene dimensions for a project."""
    return [
        eval_tests(project_path),
        eval_lint(project_path),
        eval_type_check(project_path),
        eval_coverage(project_path),
        eval_guard_patterns(project_path),
        eval_config_parser(project_path),
    ]
