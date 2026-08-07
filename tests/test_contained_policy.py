"""The three small policies: what crosses, what is masked, and which paths are translated.

Each of these is one function whose wrong answer is invisible. A variable that does not cross gives
a run without credentials; one that crosses unmasked reaches every dry-run transcript and evidence
file; a path translated when it should not be renames a directory the payload meant literally.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained.credentials import CredentialShape, resolve_credentials, vertex_model_warning
from factory.contained.env import (
    CONTAINED_ENV_POLICY,
    CONTAINED_ENV_VAR,
    in_contained,
    is_secret_key,
    redact_env,
)
from factory.contained.paths import rewrite_argv


# --------------------------------------------------------------------------------------------
# "Am I contained?" — one answer, read through one function
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "YES", " 1 "])
def test_the_contained_marker_accepts_the_documented_truthy_spellings(value: str) -> None:
    assert in_contained({CONTAINED_ENV_VAR: value})


@pytest.mark.parametrize("value", ["0", "", "no"])
def test_anything_else_means_not_contained(value: str) -> None:
    assert not in_contained({CONTAINED_ENV_VAR: value})


def test_the_marker_is_read_from_the_real_environment_when_none_is_given() -> None:
    with patch.dict(os.environ, {CONTAINED_ENV_VAR: "1"}, clear=False):
        assert in_contained()


# --------------------------------------------------------------------------------------------
# Masking
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["ANTHROPIC_API_KEY", "github_token", "MY_SECRET", "DB_PASSWORD", "GOOGLE_CREDENTIALS"]
)
def test_credential_looking_names_are_recognised_case_insensitively(key: str) -> None:
    assert is_secret_key(key)


@pytest.mark.parametrize("key", ["FACTORY_MODEL", "CLOUD_ML_REGION", "PATH"])
def test_ordinary_names_are_not_masked(key: str) -> None:
    assert not is_secret_key(key)


def test_a_forwarded_secret_is_masked_in_a_composed_environment() -> None:
    masked = redact_env({"ANTHROPIC_API_KEY": "sk-live-1234", "FACTORY_MODEL": "m"},
                        CONTAINED_ENV_POLICY)
    assert masked["ANTHROPIC_API_KEY"] == "<redacted>"
    assert masked["FACTORY_MODEL"] == "m"


def test_a_pinned_substitution_is_never_masked() -> None:
    """Its presence is the thing being verified; hiding it would defeat the check."""
    masked = redact_env({CONTAINED_ENV_VAR: "1"}, CONTAINED_ENV_POLICY)
    assert masked[CONTAINED_ENV_VAR] == "1"


# --------------------------------------------------------------------------------------------
# Path rewriting
# --------------------------------------------------------------------------------------------


def test_a_token_that_cannot_be_resolved_at_all_is_passed_through(tmp_path: Path) -> None:
    """A prompt, a URL, or a path with a null byte. The payload is opaque by design, so anything
    that is not usable as a path has to survive untouched."""
    # The first `resolve` is the project root's; only the token's is made to fail.
    with patch("pathlib.Path.resolve", side_effect=[tmp_path, OSError("name too long")]):
        out, changes = rewrite_argv(["Build a weather CLI"], tmp_path, "/workspace/rta")
    assert out == ["Build a weather CLI"]
    assert changes == []


def test_the_project_root_itself_is_rewritten_to_the_runtime_root(tmp_path: Path) -> None:
    project = tmp_path / "rta"
    project.mkdir()
    out, changes = rewrite_argv([str(project)], project, "/workspace/rta")
    assert out == ["/workspace/rta"]
    assert changes == [(str(project), "/workspace/rta")]


def test_a_flag_that_happens_to_name_a_directory_is_left_alone(tmp_path: Path) -> None:
    out, _ = rewrite_argv(["--dir"], tmp_path, "/workspace/rta")
    assert out == ["--dir"]


def test_an_empty_token_is_left_alone(tmp_path: Path) -> None:
    out, _ = rewrite_argv([""], tmp_path, "/workspace/rta")
    assert out == [""]


# --------------------------------------------------------------------------------------------
# Which model, and where it came from — never which credential
# --------------------------------------------------------------------------------------------


def test_the_model_is_reported_with_the_variable_that_supplied_it(tmp_path: Path) -> None:
    shape = resolve_credentials(
        {"ANTHROPIC_API_KEY": "sk-live", "FACTORY_MODEL": "claude-sonnet-4-5"},
        config_path=tmp_path / "absent.toml",
    )
    assert "claude-sonnet-4-5 (from FACTORY_MODEL)" in shape.detail
    assert "sk-live" not in shape.detail


def test_the_configured_default_model_is_used_when_no_variable_is_set(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[defaults]\nmodel = "claude-opus-4"\n')
    with patch("factory.contained.credentials.FACTORY_CONFIG", config):
        shape = resolve_credentials({"ANTHROPIC_API_KEY": "sk-live"}, config_path=config)
    assert "claude-opus-4" in shape.detail


def test_an_unreadable_config_leaves_the_model_unstated_rather_than_guessed(
    tmp_path: Path
) -> None:
    """"<unset>" tells the user to pass `--model`; a guessed model 429s and reads as a network
    fault."""
    config = tmp_path / "config.toml"
    config.write_text("this is not toml = = =\n")
    with patch("factory.contained.credentials.FACTORY_CONFIG", config):
        shape = resolve_credentials({"ANTHROPIC_API_KEY": "sk-live"}, config_path=config)
    assert "<unset" in shape.detail


def test_a_vertex_setup_missing_its_adc_file_is_not_ok(tmp_path: Path) -> None:
    """All three variables can be set and the run still cannot authenticate — the ADC file is the
    thing that actually carries the credential."""
    env = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": "us-east5",
        "ANTHROPIC_VERTEX_PROJECT_ID": "p",
    }
    with patch("factory.contained.credentials.ADC_DIR", tmp_path / "gcloud"):
        shape = resolve_credentials(env, config_path=tmp_path / "absent.toml")
    assert shape.backend == "vertex" and not shape.ok
    assert "missing" in shape.detail
    assert shape.fix is not None and "application-default login" in shape.fix


def test_a_vertex_shape_with_a_model_in_the_payload_does_not_warn() -> None:
    shape = CredentialShape(backend="vertex", ok=True, detail="")
    assert vertex_model_warning(shape, ["ceo", "/p", "--model=claude-sonnet-4-5"]) is None
    assert vertex_model_warning(shape, ["ceo", "/p", "--model", "claude-sonnet-4-5"]) is None


def test_a_non_vertex_shape_never_warns_about_the_model() -> None:
    """The quota problem is a property of that Vertex project, not of the runtime."""
    shape = CredentialShape(backend="anthropic", ok=True, detail="")
    assert vertex_model_warning(shape, ["ceo", "/p"]) is None
