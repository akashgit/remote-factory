"""Tests for known-fixes YAML database and lookup logic."""

from __future__ import annotations

from pathlib import Path

from factory.build_root.known_fixes import (
    is_dead_end,
    load_known_fixes,
    lookup_fix,
    match_pattern,
)

FIXTURES = Path(__file__).parent.parent / "config" / "known-fixes.yaml"


class TestYamlSchema:
    def test_loads_without_error(self) -> None:
        db = load_known_fixes(FIXTURES)
        assert db["version"] == 1

    def test_has_universal_section(self) -> None:
        db = load_known_fixes(FIXTURES)
        assert "fixes" in db["universal"]
        assert "dead_ends" in db["universal"]

    def test_has_spring_framework_project(self) -> None:
        db = load_known_fixes(FIXTURES)
        spring = db["projects"]["spring-framework"]
        assert "fixes" in spring
        assert "dead_ends" in spring

    def test_fix_entries_have_required_fields(self) -> None:
        db = load_known_fixes(FIXTURES)
        for fix in db["projects"]["spring-framework"]["fixes"]:
            assert "id" in fix
            assert "pattern" in fix
            assert "fix_type" in fix
            assert "fix_content" in fix

    def test_dead_end_entries_have_required_fields(self) -> None:
        db = load_known_fixes(FIXTURES)
        for de in db["projects"]["spring-framework"]["dead_ends"]:
            assert "artifact" in de
            assert "reason" in de
            assert "workaround" in de


class TestPatternMatching:
    def test_match_repo_401(self) -> None:
        assert match_pattern(
            r"repo\.spring\.io.*(401|403|Could not resolve)",
            "Could not GET 'https://repo.spring.io/plugins-release/...'. "
            "Received status code 401 from server",
        )

    def test_match_repo_403(self) -> None:
        assert match_pattern(
            r"repo\.spring\.io.*(401|403|Could not resolve)",
            "repo.spring.io returned 403 Forbidden",
        )

    def test_match_repo_could_not_resolve(self) -> None:
        assert match_pattern(
            r"repo\.spring\.io.*(401|403|Could not resolve)",
            "repo.spring.io: Could not resolve artifact",
        )

    def test_no_match(self) -> None:
        assert not match_pattern(
            r"repo\.spring\.io.*(401|403|Could not resolve)",
            "Successfully downloaded from mavenCentral()",
        )

    def test_match_propdeps(self) -> None:
        assert match_pattern(
            r"propdeps-plugin.*0\.0\.9",
            "Could not resolve org.springframework.build.gradle:propdeps-plugin:0.0.9",
        )

    def test_match_docbook(self) -> None:
        assert match_pattern(
            r"docbook-reference-plugin",
            "Plugin 'docbook-reference-plugin' not found",
        )


class TestLookupFix:
    def setup_method(self) -> None:
        self.db = load_known_fixes(FIXTURES)

    def test_finds_spring_repo_401(self) -> None:
        fix = lookup_fix(
            self.db,
            "Could not GET 'https://repo.spring.io/libs-release/...'. "
            "Received status code 401 from server",
            project="spring-framework",
            version_tag="v5.2.9",
        )
        assert fix is not None
        assert fix["id"] == "spring-repo-401"
        assert fix["fix_type"] == "init_script"

    def test_finds_propdeps_substitution(self) -> None:
        fix = lookup_fix(
            self.db,
            "Could not resolve propdeps-plugin:0.0.9",
            project="spring-framework",
            version_tag="v5.2.3",
        )
        assert fix is not None
        assert fix["id"] == "propdeps-substitute"
        assert fix["fix_type"] == "substitution"

    def test_version_glob_filters(self) -> None:
        fix = lookup_fix(
            self.db,
            "Could not resolve propdeps-plugin:0.0.9",
            project="spring-framework",
            version_tag="v6.0.0",
        )
        assert fix is None

    def test_no_match_returns_none(self) -> None:
        fix = lookup_fix(
            self.db,
            "Everything resolved successfully",
            project="spring-framework",
        )
        assert fix is None

    def test_project_precedence_over_universal(self) -> None:
        db: dict = {
            "version": 1,
            "universal": {
                "fixes": [
                    {
                        "id": "generic-fix",
                        "pattern": "test-error",
                        "fix_type": "build_command",
                        "fix_content": "generic",
                    }
                ],
                "dead_ends": [],
            },
            "projects": {
                "myproject": {
                    "fixes": [
                        {
                            "id": "specific-fix",
                            "pattern": "test-error",
                            "fix_type": "build_command",
                            "fix_content": "specific",
                        }
                    ],
                    "dead_ends": [],
                }
            },
        }
        fix = lookup_fix(db, "test-error occurred", project="myproject")
        assert fix is not None
        assert fix["id"] == "specific-fix"

    def test_falls_back_to_universal(self) -> None:
        db: dict = {
            "version": 1,
            "universal": {
                "fixes": [
                    {
                        "id": "universal-fix",
                        "pattern": "common-error",
                        "fix_type": "init_script",
                        "fix_content": "fix it",
                    }
                ],
                "dead_ends": [],
            },
            "projects": {},
        }
        fix = lookup_fix(db, "common-error found", project="unknown-project")
        assert fix is not None
        assert fix["id"] == "universal-fix"


class TestDeadEnd:
    def setup_method(self) -> None:
        self.db = load_known_fixes(FIXTURES)

    def test_finds_websphere_dead_end(self) -> None:
        result = is_dead_end(
            self.db,
            "com.ibm.websphere:uow:6.0.2.17",
            project="spring-framework",
        )
        assert result is not None
        assert "exclude WebSphere" in result["workaround"]

    def test_unknown_artifact_not_dead_end(self) -> None:
        result = is_dead_end(
            self.db,
            "org.apache.commons:commons-lang3:3.12.0",
            project="spring-framework",
        )
        assert result is None

    def test_version_glob_on_dead_end(self) -> None:
        result = is_dead_end(
            self.db,
            "com.ibm.websphere:uow:6.0.2.17",
            project="spring-framework",
            version_tag="v5.3.0",
        )
        assert result is not None

    def test_dead_end_version_mismatch(self) -> None:
        db: dict = {
            "version": 1,
            "universal": {"fixes": [], "dead_ends": []},
            "projects": {
                "test": {
                    "fixes": [],
                    "dead_ends": [
                        {
                            "artifact": "com.example:thing:1.0",
                            "reason": "gone",
                            "workaround": "skip",
                            "applies_to": "v3.*",
                        }
                    ],
                }
            },
        }
        assert is_dead_end(db, "com.example:thing:1.0", project="test", version_tag="v3.1") is not None
        assert is_dead_end(db, "com.example:thing:1.0", project="test", version_tag="v4.0") is None
