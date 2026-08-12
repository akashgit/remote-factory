"""The namespace's credentials Secret — checking it, and creating it without mishandling it.

`setup` used to print an `oc create secret` line and stop, which left every freshly prepared
namespace failing `verify` on the one step that decides whether it can do any work at all. This
module closes that gap while keeping the material's exposure as small as the job allows.

Four rules hold everywhere below, and each exists because the obvious implementation breaks it:

- **Never in an argv.** `oc create secret --from-literal=KEY=value` puts the value in the process
  table for every user on the machine and into the shell history of anyone who copies the line. The
  Secret is composed as a manifest and fed to `oc apply -f -` on **stdin** instead.
- **Never in YAML.** The manifest is JSON. A key containing `:`, a newline or a leading `%` is
  ordinary in this domain and is a quoting bug waiting to happen in hand-built YAML; JSON has one
  escaping rule and `oc apply` reads it natively.
- **Never echoed.** Typed input is masked, the command printed afterwards is redacted, and any
  value that somehow appears in a subprocess's stderr is scrubbed before it is shown.
- **Never logged.** structlog records key names and value *lengths*. A log line is a file, and a
  file is the thing this whole module is trying to keep the credential out of.

The step is skipped entirely when nobody is at the keyboard. `--yes` means "do not stop to ask me",
not "invent a credential", and there is no safe default for this question.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from factory.contained import style
from factory.contained.credentials import (
    ADC_DIR,
    ADC_FILE,
    VERTEX_PINNED_ENV,
    resolve_credentials,
)
from factory.contained.k8s import ADC_SECRET_KEY, LABEL_CONTAINED, SECRET_NAME, cli
from factory.contained.prereq import Check

log = structlog.get_logger()

# The keys a credentials Secret must carry for at least one supported backend.
ANTHROPIC_KEYS = ("ANTHROPIC_API_KEY",)
# The three configuration variables *and* the credential file. The credential is the point: the
# first three only say which endpoint to talk to, so a Secret carrying just those was reported as
# "carries the Vertex configuration" while holding nothing that could authenticate.
VERTEX_KEYS = (
    "CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID", ADC_SECRET_KEY,
)

# What a Google Application Default Credentials file has to contain, by its own `type`. Checked
# before the file is uploaded because the alternative is finding out from inside a pod, where the
# failure surfaces as an authentication error several minutes into an agent call.
ADC_REQUIRED_FIELDS = {
    "authorized_user": ("client_id", "client_secret", "refresh_token"),
    "service_account": ("project_id", "private_key", "client_email"),
}

ADC_TEMPLATE = """\
{
  "type": "authorized_user",
  "client_id": "....apps.googleusercontent.com",
  "client_secret": "...",
  "refresh_token": "...",
  "quota_project_id": "your-project"        // optional
}

A service account key is also accepted; it needs "type": "service_account" plus
"project_id", "private_key" and "client_email". The usual way to produce the first
form is `gcloud auth application-default login`, which writes exactly this file to
~/.config/gcloud/application_default_credentials.json."""


# ------------------------------------------------------------------------------------------------
# Reading what is there
# ------------------------------------------------------------------------------------------------


def _run(argv: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired):
        return None


def create_secret_command(binary: str, namespace: str) -> str:
    """The manual route, for the fix line and for anyone who would rather not be walked through it.

    Shown with `...` where the material goes. It is a template, and a user who fills it in has
    chosen to put a key in their shell history; the guided path exists so that is not the only
    option.
    """
    return (
        f"factory contained --target k8s --namespace {namespace} setup   # walks you through it\n"
        f"  or by hand:\n"
        f"      {binary} create secret generic {SECRET_NAME} -n {namespace} \\\n"
        f"      --from-literal=ANTHROPIC_API_KEY=...\n"
        f"  or, for Vertex:\n"
        f"      {binary} create secret generic {SECRET_NAME} -n {namespace} \\\n"
        f"      --from-literal=CLAUDE_CODE_USE_VERTEX=1 \\\n"
        f"      --from-literal=CLOUD_ML_REGION=<region> \\\n"
        f"      --from-literal=ANTHROPIC_VERTEX_PROJECT_ID=<project> \\\n"
        f"      --from-file={ADC_SECRET_KEY}=$HOME/.config/gcloud/"
        f"application_default_credentials.json"
    )


def secret_check(binary: str, namespace: str) -> Check:
    """The Secret must exist and carry a usable backend's keys — its *keys*, never its values."""
    result = _run(cli(binary, "get", "secret", SECRET_NAME, "-n", namespace,
                      "-o", "jsonpath={.data}"))
    if result is None or result.returncode != 0:
        return Check(
            name="credentials_secret",
            ok=False,
            detail=f"secret/{SECRET_NAME} is missing from {namespace}",
            fix=create_secret_command(binary, namespace),
        )
    keys = _keys_of(result.stdout)
    if set(ANTHROPIC_KEYS) <= keys:
        return Check(name="credentials_secret", ok=True,
                     detail=f"secret/{SECRET_NAME} carries the Anthropic API key")
    if set(VERTEX_KEYS) <= keys:
        return Check(name="credentials_secret", ok=True,
                     detail=f"secret/{SECRET_NAME} carries the Vertex configuration")
    return Check(
        name="credentials_secret",
        ok=False,
        detail=(
            f"secret/{SECRET_NAME} exists but carries none of the supported backends' keys "
            f"(has: {', '.join(sorted(keys)) or 'nothing'})"
        ),
        fix=create_secret_command(binary, namespace),
    )


def _keys_of(raw: str) -> set[str]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return set()
    return set(data) if isinstance(data, dict) else set()


def secret_exists(binary: str, namespace: str) -> bool:
    result = _run(cli(binary, "get", "secret", SECRET_NAME, "-n", namespace, "-o", "name"))
    return result is not None and result.returncode == 0


# ------------------------------------------------------------------------------------------------
# Describing a value without disclosing it
# ------------------------------------------------------------------------------------------------

# Below this, an excerpt would be most of the value. Short credentials are described by length only.
_EXCERPT_FLOOR = 16


def describe_value(value: str) -> str:
    """A value's shape: enough to recognise a paste that went wrong, not enough to reuse.

    A masked prompt tells you *something* arrived; it cannot tell you *what*. The common mistake
    this catches is a copy that grabbed the surrounding quotes, or an environment variable holding
    the name of a key rather than a key.
    """
    length = len(value)
    if length < _EXCERPT_FLOOR:
        return f"{length} characters"
    return f"{length} characters, starts {value[:8]!r}, ends {value[-4:]!r}"


def redact(text: str, values: tuple[str, ...]) -> str:
    """Scrub known material out of text that is about to be shown.

    Applied to subprocess stderr. `oc` does not normally echo a Secret's contents back, but "does
    not normally" is not a property worth betting a credential on, and a malformed manifest is
    exactly the case where a parser quotes the input it choked on.
    """
    for value in values:
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    return text


# ------------------------------------------------------------------------------------------------
# Composing and applying
# ------------------------------------------------------------------------------------------------


def build_secret_manifest(namespace: str, data: dict[str, str]) -> str:
    """The Secret, as JSON.

    `stringData` rather than `data`, so the API server does the base64 and nothing here has to.
    JSON rather than YAML for the escaping reason in the module docstring — every value in here is
    attacker-shaped by accident: long, random, and full of characters YAML gives meaning to.
    """
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": SECRET_NAME,
                "namespace": namespace,
                "labels": {LABEL_CONTAINED: "true"},
            },
            "type": "Opaque",
            "stringData": data,
        },
        indent=2,
    )


def redacted_command(binary: str, namespace: str, data: dict[str, str]) -> str:
    """What was done, in a form that is readable and deliberately not runnable-with-secret."""
    literals = " \\\n      ".join(
        f"--from-literal={key}={'***' if _is_material(key) else value}"
        for key, value in data.items()
    )
    return (
        f"{binary} create secret generic {SECRET_NAME} -n {namespace} \\\n      {literals}"
    )


# Keys whose values are credentials. The rest of a backend's shape — which region, which project,
# which flag — is configuration, and printing it is how a user confirms they configured the right
# thing.
def _is_material(key: str) -> bool:
    return key == ADC_SECRET_KEY or "KEY" in key or "TOKEN" in key or "SECRET" in key


def apply_secret(binary: str, namespace: str, data: dict[str, str]) -> tuple[bool, str]:
    """Create or replace the Secret from stdin. Never raises, never echoes the material."""
    manifest = build_secret_manifest(namespace, data)
    values = tuple(data.values())
    try:
        result = subprocess.run(
            cli(binary, "apply", "-n", namespace, "-f", "-"),
            input=manifest, capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
        return False, redact(f"{type(exc).__name__}: {exc}", values)
    log.info(
        "contained_secret_applied",
        namespace=namespace,
        ok=result.returncode == 0,
        # Names and sizes. The values are the one thing that must not reach a log file.
        keys={key: len(value) for key, value in data.items()},
    )
    if result.returncode == 0:
        return True, redact((result.stdout or "").strip(), values)
    detail = redact((result.stderr or "").strip(), values).splitlines()
    return False, detail[0][:200] if detail else "no detail given"


# ------------------------------------------------------------------------------------------------
# Asking
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    """One key of the Secret, and where its value may come from."""

    key: str
    question: str
    material: bool = False
    fixed: str | None = None                    # not asked at all
    default_env: tuple[str, ...] = ()           # variables to offer as the source
    default_value: str = ""
    from_file: bool = False
    file_default: Path | None = None
    json_template: str = ""
    validate_json: bool = False


ANTHROPIC_FIELDS = (
    Field(
        key="ANTHROPIC_API_KEY",
        question="Anthropic API key",
        material=True,
        default_env=("ANTHROPIC_API_KEY",),
    ),
)

VERTEX_FIELDS = (
    Field(key="CLAUDE_CODE_USE_VERTEX", question="", fixed="1"),
    Field(key="CLOUD_ML_REGION", question="Vertex region", default_env=("CLOUD_ML_REGION",)),
    Field(
        key="ANTHROPIC_VERTEX_PROJECT_ID",
        question="Google Cloud project ID",
        default_env=("ANTHROPIC_VERTEX_PROJECT_ID",),
    ),
    Field(
        key=ADC_SECRET_KEY,
        question="Application Default Credentials file",
        material=True,
        from_file=True,
        file_default=ADC_DIR / ADC_FILE,
        json_template=ADC_TEMPLATE,
        validate_json=True,
    ),
)


@dataclass
class _Readers:
    """The three input functions, in one place so tests can supply their own.

    `tests/conftest.py` forces raw terminal reads off, and a prompt reached under pytest blocks on
    a keypress that never comes. Injecting is the only way to exercise this flow at all.
    """

    line: object = field(default=None)
    secret: object = field(default=None)
    select: object = field(default=None)

    def read_line(self, question: str, default: str | None = None) -> str | None:
        reader = self.line or style.read_line
        return reader(question, default)                                       # type: ignore[operator]

    def read_secret(self, question: str) -> str | None:
        reader = self.secret or style.read_secret
        return reader(question)                                                # type: ignore[operator]

    def read_select(self, question: str, options: list[tuple[str, str]]) -> str | None:
        reader = self.select or style.select
        return reader(question, options)                                       # type: ignore[operator]


def _collect_field(field_spec: Field, readers: _Readers) -> str | None:
    """One key's value, from whichever source the user picks. `None` means they backed out."""
    if field_spec.fixed is not None:
        return field_spec.fixed
    if not field_spec.material and not field_spec.from_file:
        default = next(
            (os.environ[name] for name in field_spec.default_env if os.environ.get(name)), ""
        ) or field_spec.default_value
        answer = readers.read_line(field_spec.question, default or None)
        if answer is None:
            return None
        return answer.strip() or default

    options = [("t", "type it now (hidden as you type)")]
    if field_spec.default_env or not field_spec.from_file:
        options.append(("e", "read it from an environment variable in this shell"))
    options.append(("f", "read it from a file on this machine"))
    options.append(("q", "cancel"))

    while True:
        source = readers.read_select(f"Where does the {field_spec.question} come from?", options)
        if source is None or source == "q":
            return None
        value = _read_from_source(source, field_spec, readers)
        if value is not None:
            return value
        # A source that could not supply a value returns here rather than aborting: choosing the
        # wrong variable name is a slip, not a decision to stop.


def _read_from_source(source: str, field_spec: Field, readers: _Readers) -> str | None:
    if source == "t":
        if field_spec.json_template:
            print(style.note("Paste the file's contents, or press Escape and choose the file "
                             "instead — which is easier for anything multi-line."))
        typed = readers.read_secret(field_spec.question + ":")
        if not typed:
            return None
        return _confirm_value(field_spec, typed, readers)
    if source == "e":
        return _from_environment(field_spec, readers)
    return _from_file(field_spec, readers)


def _from_environment(field_spec: Field, readers: _Readers) -> str | None:
    suggestion = next((name for name in field_spec.default_env), None)
    name = readers.read_line("Which environment variable?", suggestion)
    if name is None:
        return None
    name = (name.strip() or suggestion or "").strip()
    if not name:
        return None
    value = os.environ.get(name, "")
    if not value.strip():
        print(style.line(style.paint(
            f"{name} is not set in this shell (or is empty). Nothing was read.", "yellow"
        )))
        return None
    return _confirm_value(field_spec, value.strip(), readers, source=f"${name}")


def _from_file(field_spec: Field, readers: _Readers) -> str | None:
    if field_spec.json_template:
        # Before the question, not after a rejection: a template shown only once the answer is
        # wrong is a template shown to somebody who has already gone and found the wrong file.
        print(style.note("This file must contain:"))
        for chunk in field_spec.json_template.splitlines():
            print(style.line(style.dim(chunk)))
    default = str(field_spec.file_default) if field_spec.file_default else None
    typed = readers.read_line("Path to the file", default)
    if typed is None:
        return None
    path = Path((typed.strip() or default or "")).expanduser()
    if not str(path):  # pragma: no cover - Path("").expanduser() is ".", so this never fires
        return None
    try:
        content = path.read_text()
    except OSError as exc:
        print(style.line(style.paint(f"Could not read {path}: {exc.strerror or exc}", "yellow")))
        return None
    if field_spec.validate_json:
        problem = validate_adc(content)
        if problem is not None:
            print(style.line(style.paint(f"{path} is not usable: {problem}", "yellow")))
            return None
    return _confirm_value(field_spec, content, readers, source=str(path))


def validate_adc(content: str) -> str | None:
    """`None` when the text is a usable ADC document, else why it is not.

    Checked here rather than in the cluster because the cluster cannot check it: an unusable
    credential is accepted into a Secret without complaint and surfaces as an authentication error
    inside an agent call, minutes later, indistinguishable from a model outage.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return f"it is not valid JSON ({exc.msg} at line {exc.lineno})"
    if not isinstance(data, dict):
        return "it is JSON, but not an object"
    kind = str(data.get("type", ""))
    if kind not in ADC_REQUIRED_FIELDS:
        return (
            f"its \"type\" is {kind or 'missing'}; expected one of "
            f"{', '.join(sorted(ADC_REQUIRED_FIELDS))}"
        )
    missing = [name for name in ADC_REQUIRED_FIELDS[kind] if not str(data.get(name, "")).strip()]
    if missing:
        return f"a {kind} document is missing: {', '.join(missing)}"
    return None


def _confirm_value(
    field_spec: Field, value: str, readers: _Readers, source: str = "typed"
) -> str | None:
    """Show the value's shape and have it confirmed. Shape only — never the value."""
    if not field_spec.material:
        return value
    print(style.line(
        f"{style.bold(field_spec.key)}: {describe_value(value)}  {style.dim(f'({source})')}"
    ))
    answer = style.confirm("Use this?", default=True)
    return value if answer else None


# ------------------------------------------------------------------------------------------------
# The step
# ------------------------------------------------------------------------------------------------


def run_credentials_step(
    binary: str,
    namespace: str,
    *,
    interactive: bool,
    assume_yes: bool = False,
    readers: _Readers | None = None,
) -> bool:
    """Leave the namespace holding usable credentials, or say exactly how to add them.

    Returns whether a usable Secret is now in place. Never raises: every failure here is a thing to
    report and carry on from, since `verify` runs immediately afterwards and will say so again.
    """
    readers = readers or _Readers()
    existing = secret_check(binary, namespace)
    if existing.ok:
        print(style.line(style.paint(existing.detail, "green")))
        print(style.note("Nothing to do. Delete it and re-run setup if you want to change it."))
        return True

    if not interactive:
        # `--yes` is deliberately not enough. It means "do not stop to ask me", and there is no
        # answer to "which credential" that can be assumed on a user's behalf.
        print(style.line(style.paint(existing.detail, "yellow")))
        print(style.note(
            "Nobody is at the keyboard, so this step is being skipped — a credential is never "
            "chosen on your behalf. Create it with:"
        ))
        for chunk in create_secret_command(binary, namespace).splitlines():
            print(style.line(style.dim(chunk)))
        return False

    print(style.line(style.paint(existing.detail, "yellow")))
    if secret_exists(binary, namespace):
        print(style.note(
            f"A secret/{SECRET_NAME} is already there but carries no backend this factory "
            "understands. Continuing replaces it."
        ))
    print(style.note(
        "The pod reads this Secret as its environment. It stays in the namespace; the factory "
        "sends the material once, here, and never reads it back."
    ))

    data = _choose_backend(readers)
    if data is None:
        print(style.line("Skipped. Create it yourself with:"))
        for chunk in create_secret_command(binary, namespace).splitlines():
            print(style.line(style.dim(chunk)))
        return False

    print()
    print(style.line(f"About to create {style.value(f'secret/{SECRET_NAME}')} in "
                     f"{style.value(namespace)} with:"))
    for key, value in data.items():
        shown = describe_value(value) if _is_material(key) else style.value(value)
        print(style.field(key, shown, pad=34))
    if style.confirm("Create it now?", default=True) is not True:
        print(style.line("Nothing was created."))
        return False

    created, detail = apply_secret(binary, namespace, data)
    if not created:
        print(style.line(style.paint(f"Could not create the Secret: {detail}", "red")))
        print(style.note("This is usually a permissions problem. Whoever owns the namespace can "
                         "create it with the command above."))
        return False
    print(style.line(style.paint(detail or f"secret/{SECRET_NAME} created.", "green")))
    print(style.note("For the record, redacted — the material was sent on stdin, never in an "
                     "argument:"))
    for chunk in redacted_command(binary, namespace, data).splitlines():
        print(style.line(style.dim(chunk)))
    return secret_check(binary, namespace).ok


def _choose_backend(readers: _Readers) -> dict[str, str] | None:
    """Which backend, then its values. `None` means the user chose to do it themselves."""
    local = resolve_credentials()
    options = [
        ("1", "Anthropic API key"),
        ("2", "Vertex AI (Google Cloud)"),
    ]
    if local.ok and local.backend in ("anthropic", "vertex"):
        options.append(("3", f"copy what this shell is configured for ({local.backend})"))
    options.append(("s", "skip — print the command and let me do it"))

    picked = readers.read_select("Which inference backend should the pod use?", options)
    if picked is None or picked == "s":
        return None
    if picked == "3":
        return _copy_from_shell(local.backend)
    fields = ANTHROPIC_FIELDS if picked == "1" else VERTEX_FIELDS
    collected: dict[str, str] = {}
    for field_spec in fields:
        value = _collect_field(field_spec, readers)
        if value is None:
            return None
        collected[field_spec.key] = value
    if fields is VERTEX_FIELDS:
        # Not a credential, but the pod has no other route to it and the run behaves differently
        # without it — the local target pins the same value for the same reason.
        collected.update(VERTEX_PINNED_ENV)
    return collected


def _copy_from_shell(backend: str) -> dict[str, str] | None:
    """Rebuild the shape this shell already resolves, reading the ADC file where one is needed."""
    if backend == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        return {"ANTHROPIC_API_KEY": key} if key else None
    collected = {
        name: os.environ.get(name, "").strip()
        for name in ("CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_PROJECT_ID")
    }
    if not all(collected.values()):
        print(style.line(style.paint(
            "This shell's Vertex configuration is incomplete; answer the questions instead.",
            "yellow",
        )))
        return None
    try:
        content = (ADC_DIR / ADC_FILE).read_text()
    except OSError as exc:
        print(style.line(style.paint(
            f"Could not read {ADC_DIR / ADC_FILE}: {exc.strerror or exc}. Run "
            "`gcloud auth application-default login` first.", "yellow",
        )))
        return None
    problem = validate_adc(content)
    if problem is not None:
        print(style.line(style.paint(f"{ADC_DIR / ADC_FILE} is not usable: {problem}", "yellow")))
        return None
    collected[ADC_SECRET_KEY] = content
    collected.update(VERTEX_PINNED_ENV)
    return collected
