"""Tests for multi-language eval support in growth, hygiene, profile, and introspect."""

from pathlib import Path
from unittest.mock import patch

from factory.eval.growth import (
    LANG_CONFIG,
    _count_functions_regex,
    _detect_project_language,
    _find_src_dirs,
    eval_capability_surface,
    eval_observability,
)
from factory.eval.hygiene import eval_tests, eval_lint, eval_type_check
from factory.discovery.introspect import (
    _detect_framework,
    _detect_lint_command,
    _detect_project_evals,
    _detect_test_command,
    _detect_type_check_command,
    introspect_project,
)
from factory.discovery.profile import build_eval_profile, _coverage_command, _syntax_check_command
from factory.models import ProjectProfile


# ── LANG_CONFIG structure ──────────────────────────────────────────


class TestLangConfig:
    def test_six_languages_configured(self):
        assert set(LANG_CONFIG.keys()) == {"python", "rust", "go", "typescript", "javascript", "java"}

    def test_each_has_required_keys(self):
        for lang, cfg in LANG_CONFIG.items():
            assert "extensions" in cfg, f"{lang} missing extensions"
            assert "skip_dirs" in cfg, f"{lang} missing skip_dirs"
            assert "function_regex" in cfg, f"{lang} missing function_regex"
            assert "entry_point_patterns" in cfg, f"{lang} missing entry_point_patterns"

    def test_python_has_no_function_regex(self):
        assert LANG_CONFIG["python"]["function_regex"] is None


# ── _detect_project_language ───────────────────────────────────────


class TestDetectProjectLanguage:
    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        assert _detect_project_language(tmp_path) == "python"

    def test_rust_project(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        assert _detect_project_language(tmp_path) == "rust"

    def test_go_project(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        assert _detect_project_language(tmp_path) == "go"

    def test_typescript_project(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        assert _detect_project_language(tmp_path) == "typescript"

    def test_javascript_project(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        assert _detect_project_language(tmp_path) == "javascript"

    def test_java_project(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        assert _detect_project_language(tmp_path) == "java"

    def test_java_gradle_project(self, tmp_path):
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'")
        assert _detect_project_language(tmp_path) == "java"

    def test_unknown_project(self, tmp_path):
        assert _detect_project_language(tmp_path) == "unknown"


# ── _find_src_dirs multi-language ──────────────────────────────────


class TestFindSrcDirsMultiLang:
    def test_finds_rust_src(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text("fn main() {}")
        dirs = _find_src_dirs(tmp_path, "rust")
        assert any("src" in str(d) for d in dirs)

    def test_finds_go_src(self, tmp_path):
        pkg = tmp_path / "cmd"
        pkg.mkdir()
        (pkg / "main.go").write_text("package main")
        dirs = _find_src_dirs(tmp_path, "go")
        assert any("cmd" in str(d) for d in dirs)

    def test_finds_typescript_src(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "index.ts").write_text("export function hello() {}")
        dirs = _find_src_dirs(tmp_path, "typescript")
        assert any("src" in str(d) for d in dirs)

    def test_skips_language_specific_dirs(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        (target / "debug.rs").write_text("fn x() {}")
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text("pub fn y() {}")
        dirs = _find_src_dirs(tmp_path, "rust")
        assert not any("target" in str(d) for d in dirs)

    def test_finds_java_src(self, tmp_path):
        src = tmp_path / "src" / "main" / "java" / "com"
        src.mkdir(parents=True)
        (src / "App.java").write_text("public class App {}")
        dirs = _find_src_dirs(tmp_path, "java")
        assert any("src" in str(d) for d in dirs)

    def test_fallback_to_project_root(self, tmp_path):
        dirs = _find_src_dirs(tmp_path, "go")
        assert dirs == [tmp_path]


# ── Multi-language function counting ──────────────────────────────


class TestMultiLangFunctionCounting:
    def test_rust_public_functions(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "lib.rs").write_text(
            "pub fn hello() {}\n"
            "pub async fn async_hello() {}\n"
            "fn private_fn() {}\n"
        )
        pattern = LANG_CONFIG["rust"]["function_regex"]
        count = _count_functions_regex([src / "lib.rs"], pattern)
        assert count == 2

    def test_go_exported_functions(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "handler.go").write_text(
            "package handler\n\n"
            "func HandleRequest(w http.ResponseWriter, r *http.Request) {}\n"
            "func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {}\n"
            "func privateHelper() {}\n"
        )
        pattern = LANG_CONFIG["go"]["function_regex"]
        count = _count_functions_regex([pkg / "handler.go"], pattern)
        assert count == 2

    def test_typescript_functions(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.ts").write_text(
            "export function handleRequest() {}\n"
            "export async function fetchData() {}\n"
            "function privateHelper() {}\n"
        )
        pattern = LANG_CONFIG["typescript"]["function_regex"]
        count = _count_functions_regex([src / "app.ts"], pattern)
        assert count == 3

    def test_java_public_methods(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.java").write_text(
            "public class App {\n"
            "    public String getName() { return null; }\n"
            "    protected int getAge() { return 0; }\n"
            "    private void helper() {}\n"
            "    public App() {}\n"  # constructor — no return type, should NOT match
            "}\n"
        )
        pattern = LANG_CONFIG["java"]["function_regex"]
        count = _count_functions_regex([src / "App.java"], pattern)
        assert count == 2

    def test_unreadable_file_skipped(self, tmp_path):
        fake = tmp_path / "bad.rs"
        fake.write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
        pattern = LANG_CONFIG["rust"]["function_regex"]
        count = _count_functions_regex([fake], pattern)
        assert count == 0


# ── eval_capability_surface multi-language ─────────────────────────


class TestCapabilitySurfaceMultiLang:
    def test_rust_project(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.rs").write_text(
            "pub fn greet() {}\npub fn serve() {}\nfn main() {}\n"
        )
        result = eval_capability_surface(tmp_path)
        assert result["score"] > 0.0
        assert "public_fns=2" in result["details"]

    def test_go_project(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        pkg = tmp_path / "cmd"
        pkg.mkdir()
        (pkg / "main.go").write_text(
            "package main\n\nfunc Main() {}\nfunc main() {}\n"
        )
        result = eval_capability_surface(tmp_path)
        assert result["score"] > 0.0
        assert "public_fns=1" in result["details"]

    def test_typescript_project(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        src = tmp_path / "src"
        src.mkdir()
        (src / "index.ts").write_text(
            "export function hello() {}\n"
            "export async function world() {}\n"
        )
        result = eval_capability_surface(tmp_path)
        assert result["score"] > 0.0

    def test_java_project(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        src = tmp_path / "src" / "main" / "java" / "com"
        src.mkdir(parents=True)
        (src / "App.java").write_text(
            "public class App {\n"
            "    public String greet() { return \"hi\"; }\n"
            "    public static void main(String[] args) {}\n"
            "}\n"
        )
        result = eval_capability_surface(tmp_path)
        assert result["score"] > 0.0
        assert "public_fns=" in result["details"]

    def test_nonexistent_path_handled(self):
        result = eval_capability_surface(Path("/nonexistent/path"))
        assert result["score"] == 0.0


# ── eval_observability language detection ──────────────────────────


class TestObservabilityLanguageDetection:
    def test_passes_detected_language(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        with patch("factory.study._analyze_observability") as mock_obs:
            mock_obs.return_value = {
                "observability_score": 0.5,
                "function_coverage": 0.3,
                "structured_logging": False,
            }
            eval_observability(tmp_path)
            mock_obs.assert_called_once_with(tmp_path, "rust")

    def test_python_project_passes_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        with patch("factory.study._analyze_observability") as mock_obs:
            mock_obs.return_value = {
                "observability_score": 0.5,
                "function_coverage": 0.3,
                "structured_logging": False,
            }
            eval_observability(tmp_path)
            mock_obs.assert_called_once_with(tmp_path, "python")


# ── Framework detection (Rust/Go) ─────────────────────────────────


class TestFrameworkDetectionRustGo:
    def test_rust_actix_web(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\nactix-web = "4"\n'
        )
        assert _detect_framework(tmp_path, "rust") == "actix-web"

    def test_rust_axum(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\naxum = "0.7"\n'
        )
        assert _detect_framework(tmp_path, "rust") == "axum"

    def test_rust_rocket(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\nrocket = "0.5"\n'
        )
        assert _detect_framework(tmp_path, "rust") == "rocket"

    def test_go_gin(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/x\nrequire github.com/gin-gonic/gin v1.9.0\n"
        )
        assert _detect_framework(tmp_path, "go") == "gin"

    def test_go_echo(self, tmp_path):
        (tmp_path / "go.sum").write_text(
            "github.com/labstack/echo/v4 v4.11.0 h1:abc\n"
        )
        assert _detect_framework(tmp_path, "go") == "echo"

    def test_go_fiber(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/x\nrequire github.com/gofiber/fiber/v2 v2.50.0\n"
        )
        assert _detect_framework(tmp_path, "go") == "fiber"

    def test_java_spring_boot(self, tmp_path):
        (tmp_path / "pom.xml").write_text(
            "<project><dependency>spring-boot-starter</dependency></project>"
        )
        assert _detect_framework(tmp_path, "java") == "spring-boot"

    def test_java_quarkus(self, tmp_path):
        (tmp_path / "build.gradle").write_text(
            "dependencies { implementation 'io.quarkus:quarkus-core' }"
        )
        assert _detect_framework(tmp_path, "java") == "quarkus"

    def test_java_micronaut(self, tmp_path):
        (tmp_path / "pom.xml").write_text(
            "<project><dependency>micronaut-core</dependency></project>"
        )
        assert _detect_framework(tmp_path, "java") == "micronaut"

    def test_no_framework(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        assert _detect_framework(tmp_path, "rust") is None


# ── Eval discovery (.sh scripts) ──────────────────────────────────


class TestEvalDiscoverySh:
    def test_discovers_sh_in_eval_dir(self, tmp_path):
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "run_bench.sh").write_text("#!/bin/bash\necho done")
        evals = _detect_project_evals(tmp_path)
        names = [e["name"] for e in evals]
        assert "run_bench" in names
        cmd = next(e for e in evals if e["name"] == "run_bench")
        assert cmd["command"] == "bash eval/run_bench.sh"

    def test_discovers_top_level_sh(self, tmp_path):
        (tmp_path / "benchmark.sh").write_text("#!/bin/bash\necho done")
        evals = _detect_project_evals(tmp_path)
        names = [e["name"] for e in evals]
        assert "benchmark" in names

    def test_py_and_sh_coexist(self, tmp_path):
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "accuracy.py").write_text("print('ok')")
        (eval_dir / "speed.sh").write_text("#!/bin/bash\necho fast")
        evals = _detect_project_evals(tmp_path)
        names = [e["name"] for e in evals]
        assert "accuracy" in names
        assert "speed" in names


# ── Profile coverage command ───────────────────────────────────────


class TestCoverageCommand:
    def _make_profile(self, language: str, **kwargs) -> ProjectProfile:
        return ProjectProfile(
            name="test-project",
            language=language,
            framework=None,
            project_type="cli_tool",
            has_tests=True,
            has_linter=False,
            has_type_checker=False,
            has_ci=False,
            test_command="test",
            lint_command=None,
            type_check_command=None,
            package_manager=kwargs.get("package_manager"),
            discovered_evals=[],
        )

    def test_python_coverage(self):
        profile = self._make_profile("python", package_manager="uv")
        cmd = _coverage_command(profile)
        assert cmd is not None
        assert "pytest --cov=" in cmd
        assert cmd.startswith("uv run")

    def test_rust_coverage(self):
        cmd = _coverage_command(self._make_profile("rust"))
        assert cmd is not None
        assert "cargo llvm-cov --summary-only" in cmd

    def test_go_coverage(self):
        cmd = _coverage_command(self._make_profile("go"))
        assert cmd is not None
        assert "go test -cover" in cmd

    def test_typescript_coverage(self):
        cmd = _coverage_command(self._make_profile("typescript"))
        assert cmd is not None
        assert "jest --coverage" in cmd

    def test_java_coverage(self):
        cmd = _coverage_command(self._make_profile("java"))
        assert cmd is not None
        assert "jacoco" in cmd

    def test_unknown_returns_none(self):
        assert _coverage_command(self._make_profile("unknown")) is None


# ── Profile coverage not gated to Python ───────────────────────────


class TestProfileCoverageNotPythonGated:
    def test_rust_project_gets_coverage_dimension(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        proj = introspect_project(tmp_path)
        profile = build_eval_profile(proj)
        dim_names = [d.name for d in profile.dimensions]
        assert "coverage" in dim_names

    def test_go_project_gets_coverage_dimension(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        proj = introspect_project(tmp_path)
        profile = build_eval_profile(proj)
        dim_names = [d.name for d in profile.dimensions]
        assert "coverage" in dim_names

    def test_typescript_project_gets_coverage_dimension(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x","scripts":{"test":"jest"}}')
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        proj = introspect_project(tmp_path)
        profile = build_eval_profile(proj)
        dim_names = [d.name for d in profile.dimensions]
        assert "coverage" in dim_names


# ── _detect_test_command for Java ─────────────────────────────────


class TestDetectTestCommandJava:
    def test_pom_xml_returns_mvn_test(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        assert _detect_test_command(tmp_path, "java") == "mvn test"

    def test_gradlew_returns_gradlew_test(self, tmp_path):
        (tmp_path / "gradlew").write_text("#!/bin/sh")
        assert _detect_test_command(tmp_path, "java") == "./gradlew test"

    def test_build_gradle_returns_gradle_test(self, tmp_path):
        (tmp_path / "build.gradle").write_text("")
        assert _detect_test_command(tmp_path, "java") == "gradle test"

    def test_build_gradle_kts_returns_gradle_test(self, tmp_path):
        (tmp_path / "build.gradle.kts").write_text("")
        assert _detect_test_command(tmp_path, "java") == "gradle test"

    def test_no_build_file_returns_none(self, tmp_path):
        assert _detect_test_command(tmp_path, "java") is None


# ── _detect_lint_command for Java ─────────────────────────────────


class TestDetectLintCommandJava:
    def test_pom_xml_returns_checkstyle(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        assert _detect_lint_command(tmp_path, "java") == "mvn checkstyle:check"

    def test_no_pom_xml_returns_none(self, tmp_path):
        (tmp_path / "build.gradle").write_text("")
        assert _detect_lint_command(tmp_path, "java") is None


# ── _detect_type_check_command for Java ───────────────────────────


class TestDetectTypeCheckCommandJava:
    def test_pom_xml_returns_mvn_compile(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        assert _detect_type_check_command(tmp_path, "java") == "mvn compile -q"

    def test_gradlew_returns_gradlew_compile(self, tmp_path):
        (tmp_path / "gradlew").write_text("#!/bin/sh")
        assert _detect_type_check_command(tmp_path, "java") == "./gradlew compileJava"

    def test_build_gradle_returns_gradle_compile(self, tmp_path):
        (tmp_path / "build.gradle").write_text("")
        assert _detect_type_check_command(tmp_path, "java") == "gradle compileJava"

    def test_build_gradle_kts_returns_gradle_compile(self, tmp_path):
        (tmp_path / "build.gradle.kts").write_text("")
        assert _detect_type_check_command(tmp_path, "java") == "gradle compileJava"

    def test_no_build_file_returns_none(self, tmp_path):
        assert _detect_type_check_command(tmp_path, "java") is None


# ── _detect_project_evals top-level .sh scripts ──────────────────


class TestDetectProjectEvalsTopLevelSh:
    def test_evaluate_sh_discovered(self, tmp_path):
        (tmp_path / "evaluate.sh").write_text("#!/bin/bash\necho done")
        evals = _detect_project_evals(tmp_path)
        match = [e for e in evals if e["name"] == "evaluate"]
        assert len(match) == 1
        assert match[0]["command"] == "bash evaluate.sh"

    def test_benchmark_sh_discovered(self, tmp_path):
        (tmp_path / "benchmark.sh").write_text("#!/bin/bash\necho done")
        evals = _detect_project_evals(tmp_path)
        match = [e for e in evals if e["name"] == "benchmark"]
        assert len(match) == 1
        assert match[0]["command"] == "bash benchmark.sh"

    def test_bench_sh_discovered(self, tmp_path):
        (tmp_path / "bench.sh").write_text("#!/bin/bash\necho done")
        evals = _detect_project_evals(tmp_path)
        match = [e for e in evals if e["name"] == "bench"]
        assert len(match) == 1
        assert match[0]["command"] == "bash bench.sh"


# ── _syntax_check_command for Java ────────────────────────────────


class TestSyntaxCheckCommandJava:
    def _make_java_profile(self, test_command: str | None = None) -> ProjectProfile:
        return ProjectProfile(
            name="test-project",
            language="java",
            framework=None,
            project_type="cli_tool",
            has_tests=test_command is not None,
            has_linter=False,
            has_type_checker=False,
            has_ci=False,
            test_command=test_command,
            lint_command=None,
            type_check_command=None,
            package_manager=None,
            discovered_evals=[],
        )

    def test_mvn_test_returns_mvn_compile(self):
        profile = self._make_java_profile("mvn test")
        assert _syntax_check_command(profile) == "mvn compile -q"

    def test_gradlew_test_returns_gradlew_compile(self):
        profile = self._make_java_profile("./gradlew test")
        assert _syntax_check_command(profile) == "./gradlew compileJava"

    def test_gradle_test_returns_gradle_compile(self):
        profile = self._make_java_profile("gradle test")
        assert _syntax_check_command(profile) == "gradle compileJava"

    def test_no_test_command_returns_true(self):
        profile = self._make_java_profile(None)
        assert _syntax_check_command(profile) == "true"


# ── _coverage_command for Java with gradlew/gradle ────────────────


class TestCoverageCommandJavaGradlew:
    def _make_java_profile(self, test_command: str) -> ProjectProfile:
        return ProjectProfile(
            name="test-project",
            language="java",
            framework=None,
            project_type="cli_tool",
            has_tests=True,
            has_linter=False,
            has_type_checker=False,
            has_ci=False,
            test_command=test_command,
            lint_command=None,
            type_check_command=None,
            package_manager=None,
            discovered_evals=[],
        )

    def test_gradlew_returns_gradlew_jacoco(self):
        profile = self._make_java_profile("./gradlew test")
        assert _coverage_command(profile) == "./gradlew jacocoTestReport"

    def test_gradle_returns_gradle_jacoco(self):
        profile = self._make_java_profile("gradle test")
        assert _coverage_command(profile) == "gradle jacocoTestReport"

    def test_mvn_returns_mvn_jacoco(self):
        profile = self._make_java_profile("mvn test")
        assert _coverage_command(profile) == "mvn jacoco:report"


# ── _detect_project_language ImportError fallback ─────────────────


class TestDetectProjectLanguageImportErrorFallback:
    """Test the ImportError fallback path in _detect_project_language.

    Setting sys.modules["factory.discovery.introspect"] to None makes
    ``from factory.discovery.introspect import _detect_language`` raise
    ImportError, triggering the fallback detection logic.
    """

    def _run_with_import_error(self, tmp_path):
        import sys
        with patch.dict(sys.modules, {"factory.discovery.introspect": None}):
            return _detect_project_language(tmp_path)

    def test_python_fallback(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert self._run_with_import_error(tmp_path) == "python"

    def test_python_setup_py_fallback(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        assert self._run_with_import_error(tmp_path) == "python"

    def test_typescript_fallback(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{}}')
        assert self._run_with_import_error(tmp_path) == "typescript"

    def test_javascript_fallback(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}')
        assert self._run_with_import_error(tmp_path) == "javascript"

    def test_rust_fallback(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
        assert self._run_with_import_error(tmp_path) == "rust"

    def test_go_fallback(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        assert self._run_with_import_error(tmp_path) == "go"

    def test_java_fallback(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        assert self._run_with_import_error(tmp_path) == "java"

    def test_unknown_fallback(self, tmp_path):
        assert self._run_with_import_error(tmp_path) == "unknown"


# ── Hygiene: polyglot independence ───────────────────────────────


class TestPolyglotIndependence:
    def test_cargo_missing_go_present(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        with (
            patch("factory.eval.hygiene.shutil.which", side_effect=lambda cmd: {
                "cargo": None, "go": "/usr/bin/go",
            }.get(cmd)),
            patch("factory.eval.hygiene._run_cmd") as mock_run,
        ):
            mock_run.return_value = (0, "ok\texample.com/x\t0.5s", "")
            result = eval_tests(tmp_path)
        assert result["score"] > 0
        assert result["name"] == "tests"


# ── Hygiene: Go eval_tests with go not on PATH ──────────────────


class TestGoTestsGoNotOnPath:
    def test_go_tests_go_not_on_path(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        with patch("factory.eval.hygiene.shutil.which", return_value=None):
            result = eval_tests(tmp_path)
        assert result["score"] == 0.5


# ── Hygiene: Java tests unparsed output ──────────────────────────


class TestJavaTestsUnparsed:
    def test_java_tests_unparsed(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        with (
            patch("factory.eval.hygiene.shutil.which", side_effect=lambda cmd: "/usr/bin/mvn" if cmd == "mvn" else None),
            patch("factory.eval.hygiene._run_cmd", return_value=(0, "BUILD SUCCESS", "")),
        ):
            result = eval_tests(tmp_path)
        assert result["score"] == 0.5


# ── Hygiene: Rust eval_lint missing cargo ────────────────────────


class TestRustLintCargoMissing:
    def test_rust_lint_cargo_missing(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        with patch("factory.eval.hygiene.shutil.which", return_value=None):
            result = eval_lint(tmp_path)
        assert result["score"] == 0.5


# ── Hygiene: polyglot shutil.which with side_effect ──────────────


class TestPolyglotShutilWhichSideEffect:
    def test_which_side_effect_routes_correctly(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        which_map = {"cargo": "/usr/bin/cargo", "go": None}
        with (
            patch("factory.eval.hygiene.shutil.which", side_effect=lambda cmd: which_map.get(cmd)),
            patch("factory.eval.hygiene._run_cmd") as mock_run,
        ):
            mock_run.return_value = (0, "test result: 5 passed; 0 failed", "")
            eval_tests(tmp_path)
        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert any(cmd[0] == "cargo" for cmd in called_cmds)
        assert not any(cmd[0] == "go" for cmd in called_cmds)


# ── Hygiene: eval_type_check Go command assertion ────────────────


class TestGoTypeCheckCommand:
    def test_go_type_check_uses_go_build(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        with (
            patch("factory.eval.hygiene.shutil.which", side_effect=lambda cmd: "/usr/bin/go" if cmd == "go" else None),
            patch("factory.eval.hygiene._run_cmd", return_value=(0, "", "")) as mock_run,
        ):
            eval_type_check(tmp_path)
        assert mock_run.called
        cmd_arg = mock_run.call_args[0][0]
        assert cmd_arg[0] == "go"
        assert cmd_arg[1] == "build"
        assert "-o" in cmd_arg
