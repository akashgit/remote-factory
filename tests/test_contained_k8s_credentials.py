"""The guided credentials Secret: what it composes, what it refuses, and what it never discloses.

The disclosure tests are the ones worth having. A wizard that collects an API key has exactly one
way to be dangerous, and it is not "the wrong key ends up in the Secret" — it is the key ending up
somewhere nobody was looking: an argv, a log line, a printed command, an error message.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.contained import k8s_credentials as creds
from factory.contained.k8s import ADC_SECRET_KEY, SECRET_NAME

SECRET = "sk-ant-api03-averylongsecretvaluethatmustnotleak-9f2c"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _Answers:
    """A scripted stand-in for the three readers, so the flow can run without a terminal.

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
# The manifest
# --------------------------------------------------------------------------------------------


def test_the_manifest_is_json_so_no_value_can_break_it() -> None:
    """A key with a colon, a quote or a newline in it is ordinary here and fatal to hand-built YAML."""
    hostile = 'a: "b"\nc: {d}\n%e\n\t--- '
    manifest = creds.build_secret_manifest("ns", {"ANTHROPIC_API_KEY": hostile})
    parsed = json.loads(manifest)
    assert parsed["stringData"]["ANTHROPIC_API_KEY"] == hostile
    assert parsed["kind"] == "Secret"
    assert parsed["metadata"]["name"] == SECRET_NAME
    assert parsed["metadata"]["namespace"] == "ns"


def test_the_manifest_uses_string_data_so_nothing_has_to_base64_by_hand() -> None:
    parsed = json.loads(creds.build_secret_manifest("ns", {"ANTHROPIC_API_KEY": SECRET}))
    assert "data" not in parsed
    assert parsed["stringData"]["ANTHROPIC_API_KEY"] == SECRET


def test_the_material_never_reaches_an_argv() -> None:
    """`--from-literal` puts a key in the process table and in the caller's shell history."""
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["stdin"] = kwargs.get("input", "")
        return _completed("secret/factory-credentials created")

    with patch("factory.contained.k8s_credentials.subprocess.run", side_effect=fake_run):
        ok, detail = creds.apply_secret("oc", "ns", {"ANTHROPIC_API_KEY": SECRET})

    assert ok
    assert SECRET not in " ".join(seen["argv"])                # type: ignore[arg-type]
    assert not any("from-literal" in token for token in seen["argv"])   # type: ignore[union-attr]
    assert SECRET in seen["stdin"]                             # type: ignore[operator]


def test_the_echoed_command_is_redacted() -> None:
    line = creds.redacted_command("oc", "ns", {"ANTHROPIC_API_KEY": SECRET})
    assert SECRET not in line
    assert "***" in line


def test_configuration_stays_readable_while_material_is_hidden() -> None:
    """A user confirms they configured the right region; hiding that helps nobody."""
    line = creds.redacted_command("oc", "ns", {
        "CLOUD_ML_REGION": "us-east5",
        ADC_SECRET_KEY: '{"type": "authorized_user"}',
    })
    assert "us-east5" in line
    assert "authorized_user" not in line


def test_a_failure_message_cannot_carry_the_value_back_out() -> None:
    """A parser quoting the input it choked on is exactly how a key reaches a terminal."""
    with patch("factory.contained.k8s_credentials.subprocess.run",
               return_value=_completed(returncode=1, stderr=f"error validating {SECRET}")):
        ok, detail = creds.apply_secret("oc", "ns", {"ANTHROPIC_API_KEY": SECRET})
    assert not ok
    assert SECRET not in detail
    assert "***" in detail


def test_nothing_but_names_and_lengths_is_logged() -> None:
    recorded: list[dict] = []
    with patch("factory.contained.k8s_credentials.subprocess.run",
               return_value=_completed("created")), \
         patch.object(creds.log, "info", lambda event, **kw: recorded.append(kw)):
        creds.apply_secret("oc", "ns", {"ANTHROPIC_API_KEY": SECRET})
    assert recorded
    assert SECRET not in json.dumps(recorded)
    assert recorded[0]["keys"] == {"ANTHROPIC_API_KEY": len(SECRET)}


# --------------------------------------------------------------------------------------------
# Describing a value without disclosing it
# --------------------------------------------------------------------------------------------


def test_a_value_is_described_by_shape_not_by_content() -> None:
    described = creds.describe_value(SECRET)
    assert str(len(SECRET)) in described
    assert SECRET not in described
    # Enough to catch a paste that grabbed the quotes, and no more.
    assert "sk-ant-a" in described


def test_a_short_value_gets_no_excerpt_at_all() -> None:
    """An excerpt of a twelve-character secret is most of the secret."""
    described = creds.describe_value("short-key-1")
    assert described == "11 characters"


# --------------------------------------------------------------------------------------------
# The ADC file
# --------------------------------------------------------------------------------------------


def test_an_authorized_user_document_is_accepted() -> None:
    assert creds.validate_adc(json.dumps({
        "type": "authorized_user", "client_id": "a", "client_secret": "b", "refresh_token": "c",
    })) is None


def test_a_service_account_key_is_accepted_too() -> None:
    assert creds.validate_adc(json.dumps({
        "type": "service_account", "project_id": "p", "private_key": "k", "client_email": "e",
    })) is None


def test_a_document_missing_a_required_field_names_the_field() -> None:
    problem = creds.validate_adc(json.dumps({"type": "authorized_user", "client_id": "a"}))
    assert problem is not None
    assert "client_secret" in problem and "refresh_token" in problem


def test_a_non_json_file_is_refused_before_it_is_uploaded() -> None:
    """Otherwise the failure surfaces inside an agent call and reads as a model outage."""
    problem = creds.validate_adc("not json at all")
    assert problem is not None and "not valid JSON" in problem


def test_a_json_document_of_an_unknown_type_is_refused() -> None:
    problem = creds.validate_adc(json.dumps({"type": "something_else"}))
    assert problem is not None and "authorized_user" in problem


# --------------------------------------------------------------------------------------------
# The step
# --------------------------------------------------------------------------------------------


def test_an_existing_usable_secret_asks_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    payload = json.dumps({"ANTHROPIC_API_KEY": "x"})
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(payload)):
        # No readers supplied: reaching a prompt at all would raise here.
        assert creds.run_credentials_step("oc", "ns", interactive=True) is True
    assert "Nothing to do" in capsys.readouterr().out


def test_nobody_at_the_keyboard_means_no_credential_is_chosen(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--yes` means "do not stop to ask me", not "pick something for me"."""
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(returncode=1)):
        created = creds.run_credentials_step(
            "oc", "ns", interactive=False, assume_yes=True, readers=creds._Readers()
        )
    assert created is False
    printed = capsys.readouterr().out
    assert "skipped" in printed
    assert "oc create secret generic" in printed


def test_the_anthropic_path_types_a_key_and_applies_it() -> None:
    answers = _Answers(selects=["1", "t"], secrets=[SECRET])
    applied: dict[str, dict[str, str]] = {}

    def fake_apply(binary, namespace, data):
        applied["data"] = data
        return True, "secret/factory-credentials created"

    with patch("factory.contained.k8s_credentials._run", return_value=_completed(returncode=1)), \
         patch("factory.contained.k8s_credentials.apply_secret", side_effect=fake_apply), \
         patch("factory.contained.k8s_credentials.secret_exists", return_value=False), \
         patch("factory.contained.k8s_credentials.style.confirm", return_value=True), \
         patch("factory.contained.k8s_credentials.secret_check") as check:
        check.side_effect = [
            creds.Check("credentials_secret", False, "missing", fix="..."),
            creds.Check("credentials_secret", True, "present"),
        ]
        assert creds.run_credentials_step(
            "oc", "ns", interactive=True, readers=answers.readers()
        ) is True
    assert applied["data"] == {"ANTHROPIC_API_KEY": SECRET}


def test_a_key_can_come_from_an_environment_variable_by_name(monkeypatch) -> None:
    monkeypatch.setenv("MY_OWN_KEY", SECRET)
    answers = _Answers(selects=["1", "e"], lines=["MY_OWN_KEY"])
    with patch("factory.contained.k8s_credentials.style.confirm", return_value=True):
        data = creds._choose_backend(answers.readers())
    assert data == {"ANTHROPIC_API_KEY": SECRET}


def test_an_unset_environment_variable_reads_nothing_and_asks_again(monkeypatch) -> None:
    """Naming the wrong variable is a slip, not a decision to stop."""
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    monkeypatch.setenv("THE_RIGHT_ONE", SECRET)
    answers = _Answers(selects=["1", "e", "e"], lines=["NOT_SET_ANYWHERE", "THE_RIGHT_ONE"])
    with patch("factory.contained.k8s_credentials.style.confirm", return_value=True):
        data = creds._choose_backend(answers.readers())
    assert data == {"ANTHROPIC_API_KEY": SECRET}


def test_the_vertex_path_reads_the_credential_from_a_file(tmp_path: Path, monkeypatch) -> None:
    adc = tmp_path / "adc.json"
    document = json.dumps({
        "type": "authorized_user", "client_id": "a", "client_secret": "b", "refresh_token": "c",
    })
    adc.write_text(document)
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    answers = _Answers(selects=["2", "f"], lines=["us-east5", "my-project", str(adc)])
    with patch("factory.contained.k8s_credentials.style.confirm", return_value=True):
        data = creds._choose_backend(answers.readers())
    assert data is not None
    assert data["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert data["CLOUD_ML_REGION"] == "us-east5"
    assert data["ANTHROPIC_VERTEX_PROJECT_ID"] == "my-project"
    assert data[ADC_SECRET_KEY] == document
    # The pinned setting the local target applies too; without it the cluster run behaves
    # differently from the same run on this machine.
    assert data["MAX_THINKING_TOKENS"] == "0"


def test_a_vertex_secret_built_here_satisfies_the_check_that_reads_it() -> None:
    """The two halves must agree: a Secret this composes has to be one `secret_check` accepts."""
    monkey = {
        "CLAUDE_CODE_USE_VERTEX": "1", "CLOUD_ML_REGION": "us-east5",
        "ANTHROPIC_VERTEX_PROJECT_ID": "p", ADC_SECRET_KEY: "{}",
    }
    payload = json.dumps({key: "x" for key in monkey})
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(payload)):
        assert creds.secret_check("oc", "ns").ok


def test_backing_out_creates_nothing_and_prints_the_manual_route(
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = _Answers(selects=["s"])
    with patch("factory.contained.k8s_credentials._run", return_value=_completed(returncode=1)), \
         patch("factory.contained.k8s_credentials.secret_exists", return_value=False), \
         patch("factory.contained.k8s_credentials.apply_secret") as apply:
        created = creds.run_credentials_step(
            "oc", "ns", interactive=True, readers=answers.readers()
        )
    assert created is False
    apply.assert_not_called()
    assert "oc create secret generic" in capsys.readouterr().out
