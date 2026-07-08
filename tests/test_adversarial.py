"""Tests for adversarial (GAN-style) eval loop support."""

import json
from pathlib import Path

import pytest

from factory.adversarial import (
    detect_convergence,
    format_adversarial_state,
    get_active_component,
    get_active_phase,
    load_adversarial_state,
    record_phase_result,
    reset_adversarial_state,
    save_adversarial_state,
    should_switch_phase,
)
from factory.models import (
    AdversarialComponent,
    AdversarialConfig,
    AdversarialPhaseRecord,
    AdversarialState,
    FactoryConfig,
)
from factory.store import _parse_adversarial


# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def gen_component() -> AdversarialComponent:
    return AdversarialComponent(
        role="generator",
        eval_command="python eval/gen.py",
        metric_name="evasion_rate",
        threshold=0.4,
        scope=["src/gen.py"],
    )


@pytest.fixture
def disc_component() -> AdversarialComponent:
    return AdversarialComponent(
        role="discriminator",
        eval_command="python eval/disc.py",
        metric_name="recall_specificity",
        threshold=0.8,
        scope=["src/disc.py"],
    )


@pytest.fixture
def adv_config(gen_component, disc_component) -> AdversarialConfig:
    return AdversarialConfig(
        generator=gen_component,
        discriminator=disc_component,
        hysteresis=3,
        convergence_window=5,
    )


@pytest.fixture
def adv_project(tmp_path: Path) -> Path:
    """Create a minimal project with .factory/ directory."""
    project = tmp_path / "adv-project"
    project.mkdir()
    (project / ".factory").mkdir()
    return project


# ── model tests ─────────────────────────────────────────────────


class TestAdversarialComponentModel:
    def test_valid_generator(self):
        c = AdversarialComponent(
            role="generator",
            eval_command="python eval.py",
            metric_name="score",
            threshold=0.5,
        )
        assert c.role == "generator"
        assert c.threshold == 0.5

    def test_valid_discriminator(self):
        c = AdversarialComponent(
            role="discriminator",
            eval_command="python eval.py",
            metric_name="accuracy",
            threshold=0.9,
        )
        assert c.role == "discriminator"

    def test_defaults(self):
        c = AdversarialComponent(
            role="generator",
            eval_command="echo ok",
            metric_name="m",
            threshold=0.5,
        )
        assert c.scope == []
        assert c.timeout == 300.0

    def test_strict_rejects_extras(self):
        with pytest.raises(Exception):
            AdversarialComponent(
                role="generator",
                eval_command="echo ok",
                metric_name="m",
                threshold=0.5,
                extra_field="bad",
            )

    def test_invalid_role_rejected(self):
        with pytest.raises(Exception):
            AdversarialComponent(
                role="attacker",
                eval_command="echo ok",
                metric_name="m",
                threshold=0.5,
            )


class TestAdversarialConfigModel:
    def test_valid_config(self, adv_config):
        assert adv_config.hysteresis == 3
        assert adv_config.generator.role == "generator"
        assert adv_config.discriminator.role == "discriminator"

    def test_defaults(self, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
        )
        assert config.hysteresis == 3
        assert config.max_rounds is None
        assert config.convergence_window == 5

    def test_strict_rejects_extras(self, gen_component, disc_component):
        with pytest.raises(Exception):
            AdversarialConfig(
                generator=gen_component,
                discriminator=disc_component,
                extra="bad",
            )

    def test_with_max_rounds(self, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            max_rounds=50,
        )
        assert config.max_rounds == 50


class TestAdversarialPhaseRecordModel:
    def test_valid_record(self):
        r = AdversarialPhaseRecord(
            round=1,
            active_role="generator",
            score=0.45,
            metric_name="evasion_rate",
            timestamp="2026-07-02T10:00:00",
            switched=False,
        )
        assert r.round == 1
        assert not r.switched

    def test_switched_flag(self):
        r = AdversarialPhaseRecord(
            round=3,
            active_role="generator",
            score=0.5,
            metric_name="evasion_rate",
            timestamp="2026-07-02T10:00:00",
            switched=True,
        )
        assert r.switched


class TestAdversarialStateModel:
    def test_default_state(self):
        state = AdversarialState()
        assert state.active_role == "generator"
        assert state.current_round == 0
        assert state.consecutive_above == 0
        assert state.generator_consecutive_above == 0
        assert state.discriminator_consecutive_above == 0
        assert not state.converged
        assert state.history == []

    def test_state_with_history(self):
        rec = AdversarialPhaseRecord(
            round=1, active_role="generator", score=0.3,
            metric_name="m", timestamp="2026-01-01T00:00:00", switched=False,
        )
        state = AdversarialState(history=[rec])
        assert len(state.history) == 1

    def test_strict_rejects_extras(self):
        with pytest.raises(Exception):
            AdversarialState(extra="bad")


# ── state persistence tests ────────────────────────────────────


class TestLoadAdversarialState:
    def test_missing_file_returns_default(self, adv_project):
        state = load_adversarial_state(adv_project)
        assert state.active_role == "generator"
        assert state.current_round == 0

    def test_reads_existing_file(self, adv_project):
        state = AdversarialState(active_role="discriminator", current_round=5)
        (adv_project / ".factory" / "adversarial_state.json").write_text(
            json.dumps(state.model_dump(), indent=2)
        )
        loaded = load_adversarial_state(adv_project)
        assert loaded.active_role == "discriminator"
        assert loaded.current_round == 5

    def test_corrupt_file_returns_default(self, adv_project):
        (adv_project / ".factory" / "adversarial_state.json").write_text("not json")
        state = load_adversarial_state(adv_project)
        assert state.active_role == "generator"
        assert state.current_round == 0

    def test_invalid_fields_returns_default(self, adv_project):
        (adv_project / ".factory" / "adversarial_state.json").write_text(
            '{"active_role": "invalid_role"}'
        )
        state = load_adversarial_state(adv_project)
        assert state.active_role == "generator"


class TestSaveAdversarialState:
    def test_creates_file(self, adv_project):
        state = AdversarialState(active_role="discriminator", current_round=3)
        save_adversarial_state(adv_project, state)
        path = adv_project / ".factory" / "adversarial_state.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["active_role"] == "discriminator"
        assert data["current_round"] == 3

    def test_roundtrip(self, adv_project):
        original = AdversarialState(
            active_role="discriminator",
            current_round=7,
            consecutive_above=2,
            generator_consecutive_above=4,
            discriminator_consecutive_above=2,
        )
        save_adversarial_state(adv_project, original)
        loaded = load_adversarial_state(adv_project)
        assert loaded == original

    def test_creates_parent_dirs(self, tmp_path):
        project = tmp_path / "new-project"
        project.mkdir()
        state = AdversarialState()
        save_adversarial_state(project, state)
        assert (project / ".factory" / "adversarial_state.json").exists()


class TestResetAdversarialState:
    def test_deletes_file(self, adv_project):
        save_adversarial_state(adv_project, AdversarialState(current_round=5))
        path = adv_project / ".factory" / "adversarial_state.json"
        assert path.exists()
        reset_adversarial_state(adv_project)
        assert not path.exists()

    def test_noop_when_missing(self, adv_project):
        reset_adversarial_state(adv_project)


# ── phase transition tests ──────────────────────────────────────


class TestShouldSwitchPhase:
    def test_below_threshold_no_switch(self, adv_config):
        state = AdversarialState(consecutive_above=0)
        assert not should_switch_phase(state, adv_config, 0.2)

    def test_above_threshold_once_no_switch_with_hysteresis(self, adv_config):
        state = AdversarialState(consecutive_above=0)
        assert not should_switch_phase(state, adv_config, 0.5)

    def test_above_threshold_consecutive_triggers_switch(self, adv_config):
        state = AdversarialState(consecutive_above=2)
        assert should_switch_phase(state, adv_config, 0.5)

    def test_score_dip_resets_counter(self, adv_config):
        state = AdversarialState(consecutive_above=2)
        assert not should_switch_phase(state, adv_config, 0.1)

    def test_hysteresis_of_one(self, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            hysteresis=1,
        )
        state = AdversarialState(consecutive_above=0)
        assert should_switch_phase(state, config, 0.5)

    def test_exactly_at_threshold_counts_as_above(self, adv_config):
        state = AdversarialState(consecutive_above=2)
        assert should_switch_phase(state, adv_config, 0.4)

    def test_discriminator_threshold(self, adv_config):
        state = AdversarialState(
            active_role="discriminator",
            consecutive_above=2,
        )
        assert should_switch_phase(state, adv_config, 0.8)
        assert not should_switch_phase(state, adv_config, 0.79)


# ── convergence tests ───────────────────────────────────────────


class TestDetectConvergence:
    def test_not_converged_initially(self, adv_config):
        state = AdversarialState()
        assert not detect_convergence(state, adv_config)

    def test_converged_when_both_above(self, adv_config):
        state = AdversarialState(
            generator_consecutive_above=5,
            discriminator_consecutive_above=5,
        )
        assert detect_convergence(state, adv_config)

    def test_not_converged_when_only_generator_above(self, adv_config):
        state = AdversarialState(
            generator_consecutive_above=5,
            discriminator_consecutive_above=3,
        )
        assert not detect_convergence(state, adv_config)

    def test_not_converged_when_only_discriminator_above(self, adv_config):
        state = AdversarialState(
            generator_consecutive_above=2,
            discriminator_consecutive_above=5,
        )
        assert not detect_convergence(state, adv_config)

    def test_convergence_window_zero_converges_immediately(self, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            convergence_window=0,
        )
        state = AdversarialState()
        assert detect_convergence(state, config)

    def test_above_threshold_converges(self, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            convergence_window=2,
        )
        state = AdversarialState(
            generator_consecutive_above=3,
            discriminator_consecutive_above=2,
        )
        assert detect_convergence(state, config)


# ── active phase query tests ────────────────────────────────────


class TestGetActivePhase:
    def test_default_is_generator(self):
        state = AdversarialState()
        assert get_active_phase(state) == "generator"

    def test_returns_discriminator(self):
        state = AdversarialState(active_role="discriminator")
        assert get_active_phase(state) == "discriminator"


class TestGetActiveComponent:
    def test_generator_active(self, adv_config):
        state = AdversarialState(active_role="generator")
        component = get_active_component(adv_config, state)
        assert component.role == "generator"
        assert component.eval_command == "python eval/gen.py"

    def test_discriminator_active(self, adv_config):
        state = AdversarialState(active_role="discriminator")
        component = get_active_component(adv_config, state)
        assert component.role == "discriminator"
        assert component.eval_command == "python eval/disc.py"


# ── record phase result tests ──────────────────────────────────


class TestRecordPhaseResult:
    def test_records_round_and_increments(self, adv_project, adv_config):
        record = record_phase_result(adv_project, adv_config, 0.2)
        assert record.round == 1
        assert record.active_role == "generator"
        assert record.score == 0.2
        assert not record.switched

        state = load_adversarial_state(adv_project)
        assert state.current_round == 1
        assert state.consecutive_above == 0
        assert len(state.history) == 1

    def test_increments_consecutive_above(self, adv_project, adv_config):
        record_phase_result(adv_project, adv_config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.consecutive_above == 1
        assert state.generator_consecutive_above == 1

    def test_resets_consecutive_on_dip(self, adv_project, adv_config):
        record_phase_result(adv_project, adv_config, 0.5)
        record_phase_result(adv_project, adv_config, 0.5)
        record_phase_result(adv_project, adv_config, 0.1)
        state = load_adversarial_state(adv_project)
        assert state.consecutive_above == 0
        assert state.generator_consecutive_above == 0

    def test_switches_phase_after_hysteresis(self, adv_project, adv_config):
        record_phase_result(adv_project, adv_config, 0.5)
        record_phase_result(adv_project, adv_config, 0.5)
        record = record_phase_result(adv_project, adv_config, 0.5)
        assert record.switched

        state = load_adversarial_state(adv_project)
        assert state.active_role == "discriminator"
        assert state.consecutive_above == 0

    def test_generator_streak_preserved_after_switch(self, adv_project, adv_config):
        for _ in range(3):
            record_phase_result(adv_project, adv_config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.generator_consecutive_above == 3
        assert state.active_role == "discriminator"

    def test_discriminator_phase_scoring(self, adv_project, adv_config):
        for _ in range(3):
            record_phase_result(adv_project, adv_config, 0.5)
        record = record_phase_result(adv_project, adv_config, 0.9)
        assert record.active_role == "discriminator"
        state = load_adversarial_state(adv_project)
        assert state.discriminator_consecutive_above == 1

    def test_full_cycle_both_phases(self, adv_project, adv_config):
        for _ in range(3):
            record_phase_result(adv_project, adv_config, 0.5)
        for _ in range(3):
            record_phase_result(adv_project, adv_config, 0.9)

        state = load_adversarial_state(adv_project)
        assert state.active_role == "generator"
        assert state.current_round == 6
        assert state.generator_consecutive_above == 3
        assert state.discriminator_consecutive_above == 3

    def test_detects_convergence(self, adv_project, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            hysteresis=2,
            convergence_window=2,
        )
        record_phase_result(adv_project, config, 0.5)
        record_phase_result(adv_project, config, 0.5)
        record_phase_result(adv_project, config, 0.9)
        record_phase_result(adv_project, config, 0.9)

        state = load_adversarial_state(adv_project)
        assert state.converged
        assert state.generator_consecutive_above == 2
        assert state.discriminator_consecutive_above == 2

    def test_convergence_stays_true(self, adv_project, gen_component, disc_component):
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            hysteresis=2,
            convergence_window=2,
        )
        for _ in range(2):
            record_phase_result(adv_project, config, 0.5)
        for _ in range(2):
            record_phase_result(adv_project, config, 0.9)
        record_phase_result(adv_project, config, 0.1)

        state = load_adversarial_state(adv_project)
        assert state.converged

    def test_history_appended(self, adv_project, adv_config):
        record_phase_result(adv_project, adv_config, 0.3)
        record_phase_result(adv_project, adv_config, 0.5)
        state = load_adversarial_state(adv_project)
        assert len(state.history) == 2
        assert state.history[0].score == 0.3
        assert state.history[1].score == 0.5

    def test_metric_name_recorded(self, adv_project, adv_config):
        record = record_phase_result(adv_project, adv_config, 0.3)
        assert record.metric_name == "evasion_rate"

    def test_timestamp_recorded(self, adv_project, adv_config):
        record = record_phase_result(adv_project, adv_config, 0.3)
        assert record.timestamp


# ── format tests ────────────────────────────────────────────────


class TestFormatAdversarialState:
    def test_default_state_output(self):
        state = AdversarialState()
        output = format_adversarial_state(state)
        assert "Active phase: generator" in output
        assert "Current round: 0" in output
        assert "Converged: False" in output

    def test_with_history_shows_entries(self):
        rec = AdversarialPhaseRecord(
            round=1, active_role="generator", score=0.35,
            metric_name="evasion_rate", timestamp="2026-07-02T10:00:00",
            switched=False,
        )
        state = AdversarialState(current_round=1, history=[rec])
        output = format_adversarial_state(state)
        assert "History (1 entries)" in output
        assert "Round 1" in output
        assert "0.3500" in output

    def test_converged_state_shows_converged(self):
        state = AdversarialState(converged=True)
        output = format_adversarial_state(state)
        assert "Converged: True" in output

    def test_switch_marker(self):
        rec = AdversarialPhaseRecord(
            round=3, active_role="generator", score=0.5,
            metric_name="evasion_rate", timestamp="2026-07-02T10:00:00",
            switched=True,
        )
        state = AdversarialState(current_round=3, history=[rec])
        output = format_adversarial_state(state)
        assert "[SWITCH]" in output

    def test_truncates_long_history(self):
        records = [
            AdversarialPhaseRecord(
                round=i, active_role="generator", score=0.3,
                metric_name="m", timestamp="2026-07-02T10:00:00",
                switched=False,
            )
            for i in range(15)
        ]
        state = AdversarialState(current_round=15, history=records)
        output = format_adversarial_state(state)
        assert "5 earlier entries omitted" in output


# ── FactoryConfig integration ───────────────────────────────────


class TestFactoryConfigAdversarial:
    def test_adversarial_defaults_to_none(self, sample_config):
        assert sample_config.adversarial is None

    def test_adversarial_accepts_config(self, adv_config):
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            adversarial=adv_config,
        )
        assert config.adversarial is not None
        assert config.adversarial.generator.role == "generator"

    def test_roundtrip_through_json(self, adv_config):
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            adversarial=adv_config,
        )
        data = config.model_dump()
        restored = FactoryConfig(**data)
        assert restored.adversarial is not None
        assert restored.adversarial.hysteresis == 3
        assert restored.adversarial.generator.threshold == 0.4

    def test_json_serialization(self, adv_config):
        config = FactoryConfig(
            goal="test",
            scope=[],
            guards=[],
            eval_command="echo ok",
            eval_threshold=0.8,
            constraints=[],
            adversarial=adv_config,
        )
        text = json.dumps(config.model_dump(), indent=2)
        data = json.loads(text)
        restored = FactoryConfig(**data)
        assert restored.adversarial == adv_config


# ── store parser tests ──────────────────────────────────────────


class TestParseAdversarial:
    def test_valid_dot_notation(self):
        items = [
            "generator.eval_command: python eval/gen.py",
            "generator.metric_name: evasion_rate",
            "generator.threshold: 0.4",
            "generator.scope: src/gen.py, src/utils.py",
            "discriminator.eval_command: python eval/disc.py",
            "discriminator.metric_name: recall_specificity",
            "discriminator.threshold: 0.8",
            "hysteresis: 3",
            "convergence_window: 5",
        ]
        config = _parse_adversarial(items)
        assert config is not None
        assert config.generator.eval_command == "python eval/gen.py"
        assert config.generator.metric_name == "evasion_rate"
        assert config.generator.threshold == 0.4
        assert config.generator.scope == ["src/gen.py", "src/utils.py"]
        assert config.discriminator.eval_command == "python eval/disc.py"
        assert config.discriminator.threshold == 0.8
        assert config.hysteresis == 3
        assert config.convergence_window == 5

    def test_missing_generator_returns_none(self):
        items = [
            "discriminator.eval_command: python eval/disc.py",
            "discriminator.metric_name: accuracy",
            "discriminator.threshold: 0.8",
        ]
        assert _parse_adversarial(items) is None

    def test_missing_discriminator_returns_none(self):
        items = [
            "generator.eval_command: python eval/gen.py",
            "generator.metric_name: score",
            "generator.threshold: 0.5",
        ]
        assert _parse_adversarial(items) is None

    def test_empty_list_returns_none(self):
        assert _parse_adversarial([]) is None

    def test_non_list_returns_none(self):
        assert _parse_adversarial("not a list") is None
        assert _parse_adversarial(42.0) is None

    def test_defaults_applied(self):
        items = [
            "generator.eval_command: python gen.py",
            "discriminator.eval_command: python disc.py",
        ]
        config = _parse_adversarial(items)
        assert config is not None
        assert config.generator.metric_name == "generator_score"
        assert config.discriminator.metric_name == "discriminator_score"
        assert config.generator.threshold == 0.5
        assert config.hysteresis == 3
        assert config.convergence_window == 5
        assert config.max_rounds is None

    def test_max_rounds_parsed(self):
        items = [
            "generator.eval_command: python gen.py",
            "discriminator.eval_command: python disc.py",
            "max_rounds: 50",
        ]
        config = _parse_adversarial(items)
        assert config is not None
        assert config.max_rounds == 50

    def test_empty_scope_gives_empty_list(self):
        items = [
            "generator.eval_command: python gen.py",
            "discriminator.eval_command: python disc.py",
        ]
        config = _parse_adversarial(items)
        assert config is not None
        assert config.generator.scope == []


# ── CLI integration tests ───────────────────────────────────────


class TestCLIAdversarialState:
    def test_subcommand_registered(self):
        from factory.cli import build_parser
        parser = build_parser()
        ns = parser.parse_args(["adversarial-state", "/tmp/test"])
        assert ns.command == "adversarial-state"
        assert ns.path == "/tmp/test"

    def test_reset_flag(self):
        from factory.cli import build_parser
        parser = build_parser()
        ns = parser.parse_args(["adversarial-state", "/tmp/test", "--reset"])
        assert ns.reset is True

    def test_reset_defaults_false(self):
        from factory.cli import build_parser
        parser = build_parser()
        ns = parser.parse_args(["adversarial-state", "/tmp/test"])
        assert ns.reset is False

    def test_handler_in_dispatch(self):
        from factory.cli import cmd_adversarial_state
        assert callable(cmd_adversarial_state)

    def test_cmd_adversarial_state_inspect(self, adv_project):
        import argparse
        from factory.cli import cmd_adversarial_state

        ns = argparse.Namespace(path=str(adv_project), reset=False)
        code = cmd_adversarial_state(ns)
        assert code == 0

    def test_cmd_adversarial_state_reset(self, adv_project):
        import argparse
        from factory.cli import cmd_adversarial_state

        save_adversarial_state(adv_project, AdversarialState(current_round=5))
        ns = argparse.Namespace(path=str(adv_project), reset=True)
        code = cmd_adversarial_state(ns)
        assert code == 0
        assert not (adv_project / ".factory" / "adversarial_state.json").exists()

    def test_handlers_dict_contains_entry(self):
        from factory.cli import main
        import inspect
        source = inspect.getsource(main)
        assert '"adversarial-state"' in source


# ── edge case tests ─────────────────────────────────────────────


class TestEdgeCases:
    def test_multiple_full_cycles(self, adv_project, gen_component, disc_component):
        """Run through multiple complete generator/discriminator cycles."""
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            hysteresis=2,
            convergence_window=4,
        )
        # Generator phase: 2 rounds above threshold → switch
        for _ in range(2):
            record_phase_result(adv_project, config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.active_role == "discriminator"

        # Discriminator phase: 2 rounds above threshold → switch
        for _ in range(2):
            record_phase_result(adv_project, config, 0.9)
        state = load_adversarial_state(adv_project)
        assert state.active_role == "generator"

        # Generator phase again: 2 more above → switch
        for _ in range(2):
            record_phase_result(adv_project, config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.active_role == "discriminator"
        assert state.generator_consecutive_above == 4
        assert state.discriminator_consecutive_above == 2
        assert not state.converged  # disc only at 2, need 4

        # Discriminator phase: 2 more above → switch, disc now at 4
        for _ in range(2):
            record_phase_result(adv_project, config, 0.9)
        state = load_adversarial_state(adv_project)
        assert state.discriminator_consecutive_above == 4
        assert state.converged

    def test_failure_resets_per_role_counter(self, adv_project, adv_config):
        """Scoring below threshold resets the active role's per-role counter."""
        record_phase_result(adv_project, adv_config, 0.5)
        record_phase_result(adv_project, adv_config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.generator_consecutive_above == 2

        record_phase_result(adv_project, adv_config, 0.1)
        state = load_adversarial_state(adv_project)
        assert state.generator_consecutive_above == 0
        assert state.consecutive_above == 0

    def test_inactive_role_counter_frozen(self, adv_project, gen_component, disc_component):
        """Per-role counters don't change when the role is inactive."""
        config = AdversarialConfig(
            generator=gen_component,
            discriminator=disc_component,
            hysteresis=2,
            convergence_window=10,
        )
        for _ in range(2):
            record_phase_result(adv_project, config, 0.5)
        state = load_adversarial_state(adv_project)
        assert state.generator_consecutive_above == 2
        assert state.discriminator_consecutive_above == 0

        record_phase_result(adv_project, config, 0.1)
        state = load_adversarial_state(adv_project)
        assert state.generator_consecutive_above == 2
        assert state.discriminator_consecutive_above == 0

    def test_convergence_not_possible_without_both_active(self, adv_project, adv_config):
        """If only the generator ever runs, convergence can't happen."""
        for _ in range(10):
            record_phase_result(adv_project, adv_config, 0.2)
        state = load_adversarial_state(adv_project)
        assert not state.converged
        assert state.active_role == "generator"
