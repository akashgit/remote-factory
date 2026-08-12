"""Exhaustive-branch coverage for the guided credentials Secret.

`tests/test_contained_k8s_credentials.py` covers the disclosure guarantees — the tests worth having.
This file exists for a narrower, mechanical reason: every remaining statement and branch in
`factory.contained.k8s_credentials`. That means the shell-out failure arms, the "wrong source"
re-ask loops, each `validate_adc` verdict, and the whole `_copy_from_shell` shape reconstruction —
paths a real user hits rarely and a regression would hide in.

The interactive surface is driven exactly as the sibling file drives it: injected `_Readers` and a
patched `style.confirm`, never a terminal. No real secret-looking value appears; dummy strings only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained import k8s_credentials as creds
from factory.contained.credentials import CredentialShape
from factory.contained.k8s import ADC_SECRET_KEY, SECRET_NAME

# Long enough to trip the excerpt floor and the redaction length guard, but obviously not a key.
DUMMY = "dummy-value-1234567890-abcdefgh"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _Answers:
    """A scripted stand-in for the three readers — copied from the sibling test file.

    `tests/conftest.py` forces raw reads off and pytest's stdin raises on `input()`, so a prompt
    reached for real here would either block or abort. Injection is the only way in.
    """

    def __init__(self, *, selects: list[str], lines: list[str] | None = None,
                 secrets: list[str] | None = None) -> None:
        self.selects = list(selects)
        self.lines = list(lines or [])
        self.secrets = list(secrets or [])

    def readers(self) -> creds._Readers:
        return creds._Readers(
            line=lambda question, default=None: self.lines.pop(0) if self.lines else default,
            secret=lambda question: self.secrets.pop(0) if self.secrets else None,
            select=lambda question, options: self.selects.pop(0) if self.selects else None,
        )


# --------------------------------------------------------------------------------------------
# _run — the shell-out that swallows a missing binary
# --------------------------------------------------------------------------------------------


def test_run_returns_none_when_the_binary_is_absent() -> None:
    """A missing `oc` must read as "could not check", not crash the whole setup wizard."""
    with patch("factory.contained.k8s_credentials.subprocess.run",
               side_effect=FileNotFoundError("no oc")):
        assert creds._run(["oc", "get"]) is None


# --------------------------------------------------------------------------------------------
# Reading what is there
# --------------------------------------------------------------------------------------------


def test_a_secret_with_no_recognised_backend_is_reported_as_such() -> None:
    """It exists, but nothing in it can authenticate — the check has to say which state that is."""
    payload = json.dumps({"SOMETHING_ELSE": "x", "ANOTHER": "y"})
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(payload)):
        check = creds.secret_check("oc", "ns")
    assert check.ok is False
    assert "none of the supported backends" in check.detail
    assert "ANOTHER" in check.detail  # the keys it does carry are named


def test_keys_of_treats_unparseable_and_non_object_json_as_empty() -> None:
    assert creds._keys_of("not json at all") == set()   # JSONDecodeError arm
    assert creds._keys_of("[1, 2, 3]") == set()          # valid JSON, but not a dict
    assert creds._keys_of('{"A": 1}') == {"A"}


def test_secret_exists_mirrors_the_get_return_code() -> None:
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(returncode=0)):
        assert creds.secret_exists("oc", "ns") is True
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(returncode=1)):
        assert creds.secret_exists("oc", "ns") is False
    with patch("factory.contained.k8s_credentials._run", return_value=None):
        assert creds.secret_exists("oc", "ns") is False


# --------------------------------------------------------------------------------------------
# Describing and redacting
# --------------------------------------------------------------------------------------------


def test_a_long_value_is_described_with_both_ends() -> None:
    described = creds.describe_value(DUMMY)
    assert str(len(DUMMY)) in described
    assert "starts" in described and "ends" in described
    assert DUMMY not in described  # only the sanctioned excerpts, not the whole thing


def test_redact_skips_empty_and_too_short_values() -> None:
    """The length guard exists so a two-character value cannot turn every 'ab' into '***'."""
    text = "keep ab but scrub the-longer-value here"
    scrubbed = creds.redact(text, ("", "ab", "the-longer-value"))
    assert "ab but" in scrubbed          # short value left alone (the `>= 4` false branch)
    assert "the-longer-value" not in scrubbed
    assert "***" in scrubbed


# --------------------------------------------------------------------------------------------
# apply_secret — the exception arm
# --------------------------------------------------------------------------------------------


def test_apply_secret_never_raises_and_redacts_the_exception() -> None:
    """A subprocess failure is a thing to report; the value must not ride out on the message."""
    with patch("factory.contained.k8s_credentials.subprocess.run",
               side_effect=OSError(f"boom near {DUMMY}")):
        ok, detail = creds.apply_secret("oc", "ns", {"ANTHROPIC_API_KEY": DUMMY})
    assert ok is False
    assert DUMMY not in detail
    assert "OSError" in detail


# --------------------------------------------------------------------------------------------
# _collect_field — the back-out arms
# --------------------------------------------------------------------------------------------


def test_a_plain_field_backs_out_when_the_line_reader_returns_none() -> None:
    field = creds.Field(key="CLOUD_ML_REGION", question="region")
    readers = creds._Readers(line=lambda question, default=None: None)
    assert creds._collect_field(field, readers) is None


def test_a_material_field_backs_out_when_the_source_menu_is_cancelled() -> None:
    readers = _Answers(selects=["q"]).readers()
    assert creds._collect_field(creds.ANTHROPIC_FIELDS[0], readers) is None


# --------------------------------------------------------------------------------------------
# _read_from_source / _from_environment / _from_file
# --------------------------------------------------------------------------------------------


def test_typing_a_json_field_prints_the_paste_hint_and_backs_out_on_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ADC field's `json_template` triggers the 'paste or choose the file' note before reading."""
    adc_field = creds.VERTEX_FIELDS[-1]
    assert adc_field.json_template  # guard: this is the field with the hint
    readers = _Answers(selects=[], secrets=[""]).readers()  # types nothing
    assert creds._read_from_source("t", adc_field, readers) is None
    assert "Paste the file's contents" in capsys.readouterr().out


def test_environment_source_backs_out_when_the_name_reader_returns_none() -> None:
    field = creds.ANTHROPIC_FIELDS[0]
    readers = creds._Readers(line=lambda question, default=None: None)
    assert creds._from_environment(field, readers) is None


def test_environment_source_backs_out_when_no_name_is_given() -> None:
    """A field with no suggested variable and a blank answer has nothing to read."""
    field = creds.Field(key="CUSTOM", question="custom", material=True)  # default_env=()
    readers = creds._Readers(line=lambda question, default=None: "")
    assert creds._from_environment(field, readers) is None


def test_file_source_reads_an_unvalidated_file_for_a_plain_material_field(
    tmp_path: Path,
) -> None:
    """The API-key field has no `json_template` and no `validate_json` — both false branches."""
    blob = tmp_path / "key.txt"
    blob.write_text(DUMMY)
    readers = _Answers(selects=[], lines=[str(blob)]).readers()
    with patch("factory.contained.k8s_credentials.style.confirm", return_value=True):
        value = creds._from_file(creds.ANTHROPIC_FIELDS[0], readers)
    assert value == DUMMY


def test_file_source_backs_out_when_the_path_reader_returns_none() -> None:
    readers = creds._Readers(line=lambda question, default=None: None)
    assert creds._from_file(creds.ANTHROPIC_FIELDS[0], readers) is None


def test_file_source_reports_an_unreadable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "does-not-exist.txt"
    readers = _Answers(selects=[], lines=[str(missing)]).readers()
    assert creds._from_file(creds.ANTHROPIC_FIELDS[0], readers) is None
    assert "Could not read" in capsys.readouterr().out


def test_file_source_prints_the_template_and_refuses_an_invalid_adc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The ADC field prints its required-shape template, then rejects a bad document before upload."""
    bad = tmp_path / "adc.json"
    bad.write_text(json.dumps({"type": "authorized_user"}))  # missing fields
    readers = _Answers(selects=[], lines=[str(bad)]).readers()
    assert creds._from_file(creds.VERTEX_FIELDS[-1], readers) is None
    out = capsys.readouterr().out
    assert "This file must contain" in out     # the template, shown before the question
    assert "is not usable" in out              # the validation refusal


# --------------------------------------------------------------------------------------------
# validate_adc — every return arm
# --------------------------------------------------------------------------------------------


def test_validate_adc_rejects_non_object_json() -> None:
    problem = creds.validate_adc("123")
    assert problem is not None and "not an object" in problem


# --------------------------------------------------------------------------------------------
# _confirm_value — the non-material passthrough
# --------------------------------------------------------------------------------------------


def test_confirm_value_returns_configuration_without_asking() -> None:
    """A non-material value is configuration; it is returned as-is, no confirmation prompt."""
    field = creds.Field(key="CLOUD_ML_REGION", question="region")  # material=False
    assert creds._confirm_value(field, "us-east5", creds._Readers()) == "us-east5"


def test_confirm_value_honours_a_no_for_material() -> None:
    field = creds.ANTHROPIC_FIELDS[0]
    with patch("factory.contained.k8s_credentials.style.confirm", return_value=False):
        assert creds._confirm_value(field, DUMMY, creds._Readers()) is None


# --------------------------------------------------------------------------------------------
# run_credentials_step — the branches the happy-path tests skip
# --------------------------------------------------------------------------------------------


def test_an_unrecognised_existing_secret_warns_that_it_will_be_replaced(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """secret_check fails but the Secret object is there — the note has to say it gets replaced."""
    answers = _Answers(selects=["s"])  # then skip, to end the flow quickly
    missing = creds.Check("credentials_secret", False, "wrong keys", fix="...")
    with patch("factory.contained.k8s_credentials.secret_check", return_value=missing), \
         patch("factory.contained.k8s_credentials.secret_exists", return_value=True):
        created = creds.run_credentials_step(
            "oc", "ns", interactive=True, readers=answers.readers()
        )
    assert created is False
    assert "Continuing replaces it" in capsys.readouterr().out


def test_declining_the_final_confirm_creates_nothing(
    monkeypatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Backend chosen, then 'Create it now?' answered no — apply must not run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    shape = CredentialShape(backend="anthropic", ok=True, detail="from key")
    missing = creds.Check("credentials_secret", False, "missing", fix="...")
    with patch("factory.contained.k8s_credentials.secret_check", return_value=missing), \
         patch("factory.contained.k8s_credentials.secret_exists", return_value=False), \
         patch("factory.contained.k8s_credentials.resolve_credentials", return_value=shape), \
         patch("factory.contained.k8s_credentials.style.confirm", return_value=False), \
         patch("factory.contained.k8s_credentials.apply_secret") as apply:
        # select "3": copy from this shell, so no per-value confirm intervenes before the final one.
        created = creds.run_credentials_step(
            "oc", "ns", interactive=True, readers=_Answers(selects=["3"]).readers()
        )
    assert created is False
    apply.assert_not_called()
    assert "Nothing was created" in capsys.readouterr().out


def test_a_failed_apply_is_reported_and_returns_false(
    monkeypatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    shape = CredentialShape(backend="anthropic", ok=True, detail="from key")
    missing = creds.Check("credentials_secret", False, "missing", fix="...")
    with patch("factory.contained.k8s_credentials.secret_check", return_value=missing), \
         patch("factory.contained.k8s_credentials.secret_exists", return_value=False), \
         patch("factory.contained.k8s_credentials.resolve_credentials", return_value=shape), \
         patch("factory.contained.k8s_credentials.style.confirm", return_value=True), \
         patch("factory.contained.k8s_credentials.apply_secret",
               return_value=(False, "forbidden")):
        created = creds.run_credentials_step(
            "oc", "ns", interactive=True, readers=_Answers(selects=["3"]).readers()
        )
    assert created is False
    assert "Could not create the Secret: forbidden" in capsys.readouterr().out


# --------------------------------------------------------------------------------------------
# _choose_backend — the copy-from-shell pick and the collect back-out
# --------------------------------------------------------------------------------------------


def test_choose_backend_offers_and_routes_copy_from_shell(monkeypatch) -> None:
    """When the shell already resolves a backend, option 3 appears and dispatches to the copy."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    shape = CredentialShape(backend="anthropic", ok=True, detail="from key")
    with patch("factory.contained.k8s_credentials.resolve_credentials", return_value=shape):
        data = creds._choose_backend(_Answers(selects=["3"]).readers())
    assert data == {"ANTHROPIC_API_KEY": DUMMY}


def test_choose_backend_aborts_when_a_field_is_abandoned() -> None:
    """Pick Anthropic, then cancel the key's source menu — the whole choice returns None."""
    answers = _Answers(selects=["1", "q"])
    data = creds._choose_backend(answers.readers())
    assert data is None


# --------------------------------------------------------------------------------------------
# _copy_from_shell — every arm
# --------------------------------------------------------------------------------------------


def test_copy_from_shell_anthropic_present_and_absent(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    assert creds._copy_from_shell("anthropic") == {"ANTHROPIC_API_KEY": DUMMY}
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert creds._copy_from_shell("anthropic") is None


def test_copy_from_shell_vertex_incomplete_config_asks_instead(
    monkeypatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    assert creds._copy_from_shell("vertex") is None
    assert "configuration is incomplete" in capsys.readouterr().out


def _complete_vertex_env(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")


def test_copy_from_shell_vertex_unreadable_adc(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _complete_vertex_env(monkeypatch)
    monkeypatch.setattr(creds, "ADC_DIR", tmp_path)
    monkeypatch.setattr(creds, "ADC_FILE", "absent.json")  # never created
    assert creds._copy_from_shell("vertex") is None
    out = capsys.readouterr().out
    assert "Could not read" in out and "application-default login" in out


def test_copy_from_shell_vertex_invalid_adc(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    _complete_vertex_env(monkeypatch)
    (tmp_path / "adc.json").write_text("not json")
    monkeypatch.setattr(creds, "ADC_DIR", tmp_path)
    monkeypatch.setattr(creds, "ADC_FILE", "adc.json")
    assert creds._copy_from_shell("vertex") is None
    assert "is not usable" in capsys.readouterr().out


def test_copy_from_shell_vertex_ok_carries_config_credential_and_pinned_env(
    monkeypatch, tmp_path: Path,
) -> None:
    _complete_vertex_env(monkeypatch)
    document = json.dumps({
        "type": "authorized_user", "client_id": "a", "client_secret": "b", "refresh_token": "c",
    })
    (tmp_path / "adc.json").write_text(document)
    monkeypatch.setattr(creds, "ADC_DIR", tmp_path)
    monkeypatch.setattr(creds, "ADC_FILE", "adc.json")
    data = creds._copy_from_shell("vertex")
    assert data is not None
    assert data["CLOUD_ML_REGION"] == "us-east5"
    assert data[ADC_SECRET_KEY] == document
    assert data["MAX_THINKING_TOKENS"] == "0"  # the pinned Vertex setting merged in
    assert SECRET_NAME  # sanity: import stays used
