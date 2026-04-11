"""Tests for factory.discovery — introspection, profile building, eval generation."""



from factory.discovery.introspect import introspect_project
from factory.discovery.profile import build_eval_profile
from factory.discovery.generate import generate_eval_script, write_eval_script
from factory.models import EvalDimension, EvalProfile


class TestIntrospect:
    def test_python_project(self, python_project):
        profile = introspect_project(python_project)
        assert profile.language == "python"
        assert profile.has_tests is True
        assert profile.has_linter is True
        assert profile.test_command is not None
        assert "pytest" in profile.test_command
        assert profile.package_manager == "uv"

    def test_detects_cli_type(self, python_project):
        profile = introspect_project(python_project)
        assert profile.project_type == "cli_tool"

    def test_detects_bot_type(self, tmp_path):
        project = tmp_path / "mybot"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "mybot"\ndependencies = ["python-telegram-bot"]\n'
        )
        (project / "README.md").write_text("# Telegram Bot\nA telegram bot.\n")
        profile = introspect_project(project)
        assert profile.project_type == "bot"

    def test_detects_typescript(self, tmp_path):
        project = tmp_path / "myapp"
        project.mkdir()
        (project / "package.json").write_text('{"name":"myapp","scripts":{"test":"jest"}}')
        (project / "README.md").write_text("# My App\n")
        profile = introspect_project(project)
        assert profile.language == "typescript"

    def test_unknown_language(self, tmp_path):
        project = tmp_path / "mystery"
        project.mkdir()
        (project / "README.md").write_text("# Mystery\n")
        profile = introspect_project(project)
        assert profile.language == "unknown"

    def test_detects_ci(self, python_project):
        (python_project / ".github" / "workflows").mkdir(parents=True)
        (python_project / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
        profile = introspect_project(python_project)
        assert profile.has_ci is True

    def test_no_ci(self, python_project):
        profile = introspect_project(python_project)
        assert profile.has_ci is False

    def test_detects_framework(self, tmp_path):
        project = tmp_path / "api"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "api"\ndependencies = ["fastapi"]\n'
        )
        (project / "README.md").write_text("# API\n")
        profile = introspect_project(project)
        assert profile.framework == "fastapi"


class TestBuildEvalProfile:
    def test_python_project_gets_discovered_evals(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        assert profile.tier == "discovered"
        assert profile.confidence == 0.8
        dim_names = [d.name for d in profile.dimensions]
        assert "tests" in dim_names
        assert "lint" in dim_names

    def test_weights_sum_to_one(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        total_weight = sum(d.weight for d in profile.dimensions)
        assert abs(total_weight - 1.0) < 1e-9

    def test_fallback_for_unknown(self, tmp_path):
        project = tmp_path / "unknown"
        project.mkdir()
        (project / "README.md").write_text("# Unknown\n")
        proj = introspect_project(project)
        profile = build_eval_profile(proj)
        assert profile.tier == "fallback"
        assert profile.confidence == 0.2
        assert len(profile.dimensions) > 0

    def test_human_reviewed_default_false(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        assert profile.human_reviewed is False


class TestGenerateEvalScript:
    def test_generates_valid_python(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        script = generate_eval_script(profile)
        assert "def eval_tests" in script
        assert "def eval_lint" in script
        assert "json.dump" in script
        assert "EVALS = " in script
        # Verify it's valid Python
        compile(script, "<eval_script>", "exec")

    def test_write_eval_script_creates_file(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        path = write_eval_script(profile, python_project)
        assert path.exists()
        assert path.name == "score.py"
        assert path.parent.name == "eval"

    def test_script_has_all_dimensions(self, python_project):
        project = introspect_project(python_project)
        profile = build_eval_profile(project)
        script = generate_eval_script(profile)
        for dim in profile.dimensions:
            assert f"eval_{dim.name}" in script

    def test_command_with_quoted_args(self):
        dim = EvalDimension(
            name="integration",
            command='uv run pytest -k "test_integration"',
            weight=1.0,
            parser="exit_code",
            description="Run integration tests",
            source="discovered",
        )
        profile = EvalProfile(
            project_type="cli_tool",
            dimensions=[dim],
            tier="discovered",
            confidence=0.8,
        )
        script = generate_eval_script(profile)
        assert "['uv', 'run', 'pytest', '-k', 'test_integration']" in script

    def test_simple_command(self):
        dim = EvalDimension(
            name="unit",
            command="uv run pytest -v",
            weight=1.0,
            parser="exit_code",
            description="Run unit tests",
            source="discovered",
        )
        profile = EvalProfile(
            project_type="cli_tool",
            dimensions=[dim],
            tier="discovered",
            confidence=0.8,
        )
        script = generate_eval_script(profile)
        assert "['uv', 'run', 'pytest', '-v']" in script
