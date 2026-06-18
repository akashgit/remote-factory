"""Tests for factory.research.leakage — ground truth leakage detection."""

from __future__ import annotations

from factory.research.leakage import (
    _check_negation_hints,
    _check_specific_values,
    _check_token_overlap,
    _extract_specific_values,
    _tokenize_text,
    fingerprint_fixed_surfaces,
    scan_diff_for_leakage,
    scan_for_leakage,
    validate_research_config,
)


# ── _tokenize_text ───────────────────────────────────────────


class TestTokenizeText:
    def test_basic_identifiers(self):
        tokens = _tokenize_text("calculate_accuracy parse_findings")
        assert "calculate" in tokens
        assert "accuracy" in tokens
        assert "parse" in tokens
        assert "findings" in tokens

    def test_camel_case_split(self):
        tokens = _tokenize_text("calculateAccuracy parseResults")
        assert "calculate" in tokens
        assert "accuracy" in tokens

    def test_stopwords_filtered(self):
        tokens = _tokenize_text("def return import class self")
        assert "def" not in tokens
        assert "return" not in tokens
        assert "import" not in tokens

    def test_short_tokens_filtered(self):
        tokens = _tokenize_text("ab cd x calculateAccuracy")
        assert "ab" not in tokens
        assert "cd" not in tokens
        assert "calculate" in tokens

    def test_empty_text(self):
        assert _tokenize_text("") == set()

    def test_full_words_kept(self):
        tokens = _tokenize_text("calculateAccuracy")
        assert "calculateaccuracy" in tokens


# ── _extract_specific_values ─────────────────────────────────


class TestExtractSpecificValues:
    def test_numeric_literals(self):
        values = _extract_specific_values("accuracy = 0.847 and count = 42")
        assert "0.847" in values
        assert "42" in values

    def test_common_numbers_filtered(self):
        values = _extract_specific_values("x = 0 y = 1 z = 100 w = 0.5")
        assert "0" not in values
        assert "1" not in values
        assert "100" not in values
        assert "0.5" not in values

    def test_quoted_strings(self):
        values = _extract_specific_values('label = "expected_output" and key = \'secret_value\'')
        assert "expected_output" in values
        assert "secret_value" in values

    def test_short_quoted_strings_filtered(self):
        values = _extract_specific_values('"ab" "ok"')
        assert "ab" not in values
        assert "ok" not in values

    def test_empty_text(self):
        assert _extract_specific_values("") == set()


# ── fingerprint_fixed_surfaces ───────────────────────────────


class TestFingerprintFixedSurfaces:
    def test_extracts_tokens_from_files(self, tmp_path):
        (tmp_path / "ground_truth.py").write_text(
            "def calculate_subtraction(a, b):\n"
            "    return a - b\n"
            "EXPECTED_ACCURACY = 0.847\n"
        )
        fps = fingerprint_fixed_surfaces(tmp_path, ["ground_truth.py"])
        assert "ground_truth.py" in fps
        tokens = fps["ground_truth.py"]
        assert "calculate" in tokens
        assert "subtraction" in tokens
        assert "0.847" in tokens

    def test_empty_fixed_surfaces(self, tmp_path):
        assert fingerprint_fixed_surfaces(tmp_path, []) == {}

    def test_missing_file(self, tmp_path):
        fps = fingerprint_fixed_surfaces(tmp_path, ["nonexistent.py"])
        assert fps == {}

    def test_glob_pattern(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "expected.json").write_text(
            '{"answer": "subtraction", "score": 0.95}\n'
        )
        fps = fingerprint_fixed_surfaces(tmp_path, ["data/*.json"])
        assert len(fps) == 1
        key = next(iter(fps))
        assert "expected.json" in key

    def test_directory_ignored(self, tmp_path):
        (tmp_path / "somedir").mkdir()
        fps = fingerprint_fixed_surfaces(tmp_path, ["somedir"])
        assert fps == {}


# ── _check_token_overlap ─────────────────────────────────────


class TestCheckTokenOverlap:
    def test_high_overlap(self):
        text_tokens = {"calculate", "subtraction", "accuracy", "expected"}
        fingerprints = {
            "truth.py": {"calculate", "subtraction", "accuracy", "expected", "result"},
        }
        findings = _check_token_overlap(text_tokens, fingerprints, threshold=0.15)
        assert len(findings) == 1
        assert findings[0].leak_type == "token_overlap"
        assert findings[0].source_file == "truth.py"

    def test_no_overlap(self):
        text_tokens = {"logging", "agent", "builder"}
        fingerprints = {"truth.py": {"calculate", "subtraction", "accuracy"}}
        findings = _check_token_overlap(text_tokens, fingerprints, threshold=0.15)
        assert findings == []

    def test_empty_tokens(self):
        findings = _check_token_overlap(set(), {"truth.py": {"calculate"}}, threshold=0.15)
        assert findings == []


# ── _check_negation_hints ────────────────────────────────────


class TestCheckNegationHints:
    def test_do_not_pattern(self):
        fingerprints = {"truth.py": {"subtract", "division", "multiply"}}
        findings = _check_negation_hints("do not subtract from the result", fingerprints)
        assert len(findings) == 1
        assert findings[0].leak_type == "negation_hint"
        assert findings[0].leaked_token == "subtract"

    def test_should_not_pattern(self):
        fingerprints = {"truth.py": {"multiply"}}
        findings = _check_negation_hints("should not multiply the values", fingerprints)
        assert len(findings) == 1

    def test_avoid_pattern(self):
        fingerprints = {"truth.py": {"division"}}
        findings = _check_negation_hints("avoid division here", fingerprints)
        assert len(findings) == 1

    def test_never_pattern(self):
        fingerprints = {"truth.py": {"recursion"}}
        findings = _check_negation_hints("never recursion in this function", fingerprints)
        assert len(findings) == 1

    def test_dont_pattern(self):
        fingerprints = {"truth.py": {"subtract"}}
        findings = _check_negation_hints("don't subtract anything", fingerprints)
        assert len(findings) == 1

    def test_no_match(self):
        fingerprints = {"truth.py": {"subtract"}}
        findings = _check_negation_hints("add the values together", fingerprints)
        assert findings == []

    def test_negated_word_not_in_fingerprint(self):
        fingerprints = {"truth.py": {"subtract"}}
        findings = _check_negation_hints("do not multiply the values", fingerprints)
        assert findings == []


# ── _check_specific_values ───────────────────────────────────


class TestCheckSpecificValues:
    def test_numeric_value_leaked(self):
        fingerprints = {"truth.py": {"0.847", "calculate"}}
        findings = _check_specific_values("the expected accuracy is 0.847", fingerprints)
        assert len(findings) == 1
        assert findings[0].leaked_token == "0.847"
        assert findings[0].leak_type == "specific_value"

    def test_no_value_match(self):
        fingerprints = {"truth.py": {"0.847"}}
        findings = _check_specific_values("improve the accuracy score", fingerprints)
        assert findings == []

    def test_empty_text(self):
        fingerprints = {"truth.py": {"0.847"}}
        findings = _check_specific_values("", fingerprints)
        assert findings == []


# ── scan_for_leakage ─────────────────────────────────────────


class TestScanForLeakage:
    def test_no_leakage(self):
        fingerprints = {"truth.py": {"subtract", "division", "accuracy"}}
        report = scan_for_leakage("improve logging in the agent", fingerprints)
        assert not report.flagged
        assert report.risk_level == "none"

    def test_negation_hint_high_risk(self):
        fingerprints = {"truth.py": {"subtract"}}
        report = scan_for_leakage("do not subtract from the value", fingerprints)
        assert report.flagged
        assert report.risk_level == "high"

    def test_specific_value_medium_risk(self):
        fingerprints = {"truth.py": {"0.847"}}
        report = scan_for_leakage("target accuracy of 0.847", fingerprints)
        assert report.flagged
        assert report.risk_level in ("medium", "high")

    def test_empty_text(self):
        fingerprints = {"truth.py": {"subtract"}}
        report = scan_for_leakage("", fingerprints)
        assert not report.flagged

    def test_empty_fingerprints(self):
        report = scan_for_leakage("do not subtract", {})
        assert not report.flagged

    def test_sensitivity_levels(self):
        fingerprints = {"truth.py": {"calculate", "accuracy", "expected", "subtract"}}
        text = "calculate the accuracy"

        report_low = scan_for_leakage(text, fingerprints, sensitivity="low")
        report_high = scan_for_leakage(text, fingerprints, sensitivity="high")
        # High sensitivity should be more likely to flag than low
        if report_low.flagged:
            assert report_high.flagged


# ── scan_diff_for_leakage ────────────────────────────────────


class TestScanDiffForLeakage:
    def test_added_lines_scanned(self):
        fingerprints = {"truth.py": {"0.847"}}
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            " existing code\n"
            "+EXPECTED_VALUE = 0.847\n"
            " more code\n"
        )
        report = scan_diff_for_leakage(diff, fingerprints)
        assert report.flagged

    def test_context_lines_ignored(self):
        fingerprints = {"truth.py": {"0.847"}}
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,3 @@\n"
            " EXISTING_VALUE = 0.847\n"
            "-old line\n"
            "+new line\n"
        )
        report = scan_diff_for_leakage(diff, fingerprints)
        assert not report.flagged

    def test_empty_diff(self):
        fingerprints = {"truth.py": {"0.847"}}
        report = scan_diff_for_leakage("", fingerprints)
        assert not report.flagged

    def test_no_added_lines(self):
        fingerprints = {"truth.py": {"subtract"}}
        diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,2 @@\n"
            " existing\n"
            "-removed line with subtract\n"
        )
        report = scan_diff_for_leakage(diff, fingerprints)
        assert not report.flagged


# ── validate_research_config ─────────────────────────────────


class TestValidateResearchConfig:
    def test_valid_config(self, tmp_path):
        from factory.models import FactoryConfig, ResearchTarget

        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "truth.json").write_text("{}")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "solver.py").write_text("")

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            research_target=ResearchTarget(
                objective="test",
                metric="accuracy",
                target=0.9,
                run_command="echo ok",
                result_path="results.json",
            ),
            fixed_surfaces=["data/*.json"],
            mutable_surfaces=["src/**/*.py"],
        )
        errors = validate_research_config(config, tmp_path)
        assert errors == []

    def test_missing_research_target(self, tmp_path):
        from factory.models import FactoryConfig

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            fixed_surfaces=["data/*.json"],
            mutable_surfaces=["src/**/*.py"],
        )
        errors = validate_research_config(config, tmp_path)
        assert any("research_target" in e for e in errors)

    def test_empty_fixed_surfaces(self, tmp_path):
        from factory.models import FactoryConfig, ResearchTarget

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            research_target=ResearchTarget(
                objective="test",
                metric="accuracy",
                target=0.9,
                run_command="echo ok",
                result_path="results.json",
            ),
            fixed_surfaces=[],
            mutable_surfaces=["src/**/*.py"],
        )
        errors = validate_research_config(config, tmp_path)
        assert any("fixed_surfaces" in e for e in errors)

    def test_empty_mutable_surfaces(self, tmp_path):
        from factory.models import FactoryConfig, ResearchTarget

        (tmp_path / "truth.py").write_text("data")

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            research_target=ResearchTarget(
                objective="test",
                metric="accuracy",
                target=0.9,
                run_command="echo ok",
                result_path="results.json",
            ),
            fixed_surfaces=["truth.py"],
            mutable_surfaces=[],
        )
        errors = validate_research_config(config, tmp_path)
        assert any("mutable_surfaces" in e for e in errors)

    def test_fixed_pattern_matches_no_files(self, tmp_path):
        from factory.models import FactoryConfig, ResearchTarget

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            research_target=ResearchTarget(
                objective="test",
                metric="accuracy",
                target=0.9,
                run_command="echo ok",
                result_path="results.json",
            ),
            fixed_surfaces=["nonexistent/*.json"],
            mutable_surfaces=["src/**/*.py"],
        )
        errors = validate_research_config(config, tmp_path)
        assert any("matches no files" in e for e in errors)

    def test_mutable_fixed_overlap(self, tmp_path):
        from factory.models import FactoryConfig, ResearchTarget

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "solver.py").write_text("")

        config = FactoryConfig(
            goal="test",
            scope=["src/**"],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            research_target=ResearchTarget(
                objective="test",
                metric="accuracy",
                target=0.9,
                run_command="echo ok",
                result_path="results.json",
            ),
            fixed_surfaces=["src/**/*.py"],
            mutable_surfaces=["src/**/*.py"],
        )
        errors = validate_research_config(config, tmp_path)
        assert any("overlap" in e for e in errors)
