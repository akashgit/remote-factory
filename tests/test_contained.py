"""`factory contained` — command surface, path translation, plan composition, dry run."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.cli import contained as cli
from factory.contained.credentials import (
    CredentialShape,
    resolve_credentials,
    vertex_model_warning,
)
from factory.contained.env import CONTAINED_ENV_POLICY, redact_argv
from factory.contained.errors import ContainedError
from factory.contained.identity import Identity
from factory.contained.paths import rewrite_argv
from factory.podman import (
    CONTAINER_HOME,
    LABEL_CONTAINED,
    LABEL_PROJECT,
    ContainerPlan,
    Mount,
    build_attach_argv,
    build_create_argv,
    build_ps_argv,
    build_tmux_launch,
    container_name,
    dry_run_enabled,
    plan_steps,
)


def parse(argv: list[str]) -> argparse.Namespace:
    """Parse a `factory contained ...` command line the way the real CLI does."""
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    return parser.parse_args(["contained", *argv])


def interpret(argv: list[str]) -> argparse.Namespace:
    args = parse(argv)
    cli.interpret(cli._PARSER, args)
    return args


# --------------------------------------------------------------------------------------------
# Command surface (§2.1, §2.2, §2.3)
# --------------------------------------------------------------------------------------------


def test_payload_after_separator_is_verbatim() -> None:
    args = interpret(["--", "ceo", "/tmp/p", "--focus", "container image", "--loop"])
    assert args.subcommand is None
    assert args.factory_args == ["ceo", "/tmp/p", "--focus", "container image", "--loop"]


def test_payload_flags_are_not_parsed_as_runtime_flags() -> None:
    """A flag the host also defines must not be stolen from the payload."""
    args = interpret(["--", "ceo", "/tmp/p", "--name", "inner-name"])
    assert args.name is None
    assert args.factory_args == ["ceo", "/tmp/p", "--name", "inner-name"]


def test_explicit_name_survives_a_payload_run() -> None:
    args = interpret(["--name", "chosen", "--", "study", "/tmp/p"])
    assert args.name == "chosen"


def test_lifecycle_subcommand_takes_a_positional_name() -> None:
    args = interpret(["rm", "rta-abc123"])
    assert (args.subcommand, args.name) == ("rm", "rta-abc123")


def test_trailing_yes_reaches_the_namespace() -> None:
    args = interpret(["rm", "rta-abc123", "--yes"])
    assert args.yes is True


def test_flag_after_lifecycle_subcommand_is_an_error_not_a_name() -> None:
    with pytest.raises(SystemExit):
        interpret(["ls", "--target", "k8s"])


def test_local_only_flag_against_k8s_fails_at_parse_time() -> None:
    with pytest.raises(SystemExit):
        interpret(["--target", "k8s", "--mount", "/tmp", "--", "study", "/tmp/p"])


def test_k8s_only_flag_against_local_fails_at_parse_time() -> None:
    with pytest.raises(SystemExit):
        interpret(["--namespace", "factory", "--", "study", "/tmp/p"])


def test_lifecycle_command_needing_a_name_says_so() -> None:
    with pytest.raises(SystemExit):
        interpret(["attach"])


def test_empty_invocation_names_an_example() -> None:
    with pytest.raises(SystemExit):
        interpret([])


def test_name_is_not_abbreviated_into_namespace() -> None:
    """`--name` and `--namespace` share a prefix; abbreviation would alias them silently."""
    with pytest.raises(SystemExit):
        interpret(["--nam", "x", "--", "study", "/tmp/p"])


def test_help_lists_flags_by_target_not_as_a_flat_list() -> None:
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    p = cli.build_contained_parser(sub)
    text = p.format_help()
    assert "Both targets:" in text and "Local only:" in text and "K8s only:" in text
    # The flags appear once — in the tables — not twice.
    assert text.count("--storage-class") == 1


# --------------------------------------------------------------------------------------------
# Path translation (§2.5)
# --------------------------------------------------------------------------------------------


def test_in_project_path_is_rewritten(tmp_path: Path) -> None:
    project = tmp_path / "rta"
    (project / "eval").mkdir(parents=True)
    argv, changes = rewrite_argv(
        ["study", str(project), "--out", str(project / "eval")], project, Path("/workspace/rta")
    )
    assert argv == ["study", "/workspace/rta", "--out", "/workspace/rta/eval"]
    assert len(changes) == 2


def test_out_of_project_path_is_left_alone(tmp_path: Path) -> None:
    project = tmp_path / "rta"
    project.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    argv, changes = rewrite_argv([str(other)], project, Path("/workspace/rta"))
    assert argv == [str(other)]
    assert changes == []


def test_non_path_tokens_are_left_alone(tmp_path: Path) -> None:
    project = tmp_path / "rta"
    project.mkdir()
    payload = ["ceo", "--focus", "add a --version flag", "https://example.com", "-v"]
    argv, changes = rewrite_argv(payload, project, Path("/workspace/rta"))
    assert argv == payload
    assert changes == []


def test_rewrite_is_a_no_op_when_the_paths_coincide(tmp_path: Path) -> None:
    """The local target mounts the copy at its own absolute path, so this case is the common one."""
    project = tmp_path / "rta"
    project.mkdir()
    argv, changes = rewrite_argv([str(project)], project, project)
    assert argv == [str(project)]
    assert changes == []


# --------------------------------------------------------------------------------------------
# Credential shape and the forwarding policy (§3.5)
# --------------------------------------------------------------------------------------------


def test_api_key_shape_forwards_exactly_one_variable(tmp_path: Path) -> None:
    shape = resolve_credentials(
        {"ANTHROPIC_API_KEY": "sk-ant-secret"}, config_path=tmp_path / "absent.toml"
    )
    assert shape.backend == "anthropic"
    assert shape.ok
    assert shape.env == {"ANTHROPIC_API_KEY": "sk-ant-secret"}
    assert "sk-ant-secret" not in shape.detail


def test_vertex_shape_pins_thinking_tokens_and_mounts_adc(tmp_path: Path) -> None:
    env = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": "us-east5",
        "ANTHROPIC_VERTEX_PROJECT_ID": "some-project",
    }
    shape = resolve_credentials(env, config_path=tmp_path / "absent.toml")
    assert shape.backend == "vertex"
    assert shape.env["MAX_THINKING_TOKENS"] == "0"
    assert set(shape.env) >= set(env)


def test_missing_inference_reports_a_fix_not_a_crash(tmp_path: Path) -> None:
    shape = resolve_credentials({}, config_path=tmp_path / "absent.toml")
    assert not shape.ok
    assert shape.backend == "none"
    assert shape.fix


def test_credential_profile_in_the_mounted_config_counts_as_configured(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[credentials.vertex]\nANTHROPIC_API_KEY = "sk-ant-x"\n')
    shape = resolve_credentials({}, config_path=config)
    assert shape.ok
    assert shape.backend == "profile"
    assert shape.env == {}          # nothing crosses; ~/.factory is mounted
    assert "sk-ant-x" not in shape.detail


def test_vertex_without_an_explicit_model_warns() -> None:
    shape = CredentialShape(backend="vertex", ok=True, detail="")
    assert vertex_model_warning(shape, ["ceo", "/tmp/p"]) is not None
    assert vertex_model_warning(shape, ["ceo", "/tmp/p", "--model", "claude-sonnet-4-5"]) is None
    assert vertex_model_warning(shape, ["ceo", "/tmp/p", "--model=x"]) is None


def test_nothing_unnamed_crosses_the_boundary() -> None:
    environ = {
        "FACTORY_MODEL": "claude-sonnet-4-5",
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "OPENAI_API_KEY": "sk-openai",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "PATH": "/usr/bin",
    }
    crossed = CONTAINED_ENV_POLICY.resolve(environ)
    assert crossed["FACTORY_MODEL"] == "claude-sonnet-4-5"
    assert "ANTHROPIC_API_KEY" not in crossed     # only via --forward or the resolved shape
    assert "OPENAI_API_KEY" not in crossed
    assert "AWS_SECRET_ACCESS_KEY" not in crossed
    assert "PATH" not in crossed
    assert crossed["FACTORY_CONTAINED"] == "1"


def test_host_only_factory_controls_do_not_cross() -> None:
    crossed = CONTAINED_ENV_POLICY.resolve(
        {
            "FACTORY_CONTAINED_DRY_RUN": "1",
            "FACTORY_CONTAINED_HOME": "/host/path",
            "FACTORY_CONTAINED_IMAGE": "ref",
            "FACTORY_RUNNER": "claude",
        }
    )
    assert crossed == {"FACTORY_CONTAINED": "1", "FACTORY_RUNNER": "claude"}


def test_secret_values_are_redacted_in_a_composed_command() -> None:
    argv = ["podman", "run", "--env", "ANTHROPIC_API_KEY=sk-ant-secret",
            "--env", "FACTORY_MODEL=claude-sonnet-4-5"]
    rendered = " ".join(redact_argv(argv, CONTAINED_ENV_POLICY))
    assert "sk-ant-secret" not in rendered
    assert "FACTORY_MODEL=claude-sonnet-4-5" in rendered


# --------------------------------------------------------------------------------------------
# Podman command composition (§3.1, §3.4, §5.3)
# --------------------------------------------------------------------------------------------


def _plan(tmp_path: Path) -> ContainerPlan:
    workspace = tmp_path / "rta"
    workspace.mkdir(exist_ok=True)
    return ContainerPlan(
        name="rta-abc123",
        image="example/runtime:latest",
        workdir=str(workspace),
        env={"FACTORY_CONTAINED": "1", "HOME": CONTAINER_HOME},
        labels={LABEL_CONTAINED: "true", LABEL_PROJECT: "deadbeef"},
        mounts=(Mount(workspace, str(workspace)),),
        run_command=f"cd {workspace} && factory study {workspace}",
        user="501:0",
    )


def test_create_carries_init_labels_mounts_and_identity(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    argv = build_create_argv(plan)
    assert argv[:4] == ["podman", "run", "-d", "--init"]
    assert f"{LABEL_CONTAINED}=true" in argv
    assert plan.mounts[0].as_flag() in argv
    assert "--user" in argv and "501:0" in argv
    assert argv[-3:] == ["sh", "-lc", "sleep infinity"]


def test_workspace_is_mounted_at_its_own_absolute_path(tmp_path: Path) -> None:
    """Path-preserving is load-bearing: the local division's builds run outside the container."""
    plan = _plan(tmp_path)
    source, target, mode = plan.mounts[0].as_flag().split(":")
    assert source == target == plan.workdir
    assert mode == "rw"


def test_ps_selects_only_factory_created_containers() -> None:
    argv = build_ps_argv()
    assert "--filter" in argv
    assert f"label={LABEL_CONTAINED}=true" in argv
    assert "--all" in argv


def test_attach_goes_through_tmux_with_a_tty() -> None:
    argv = build_attach_argv("rta-abc123")
    assert argv[:2] == ["podman", "exec"]
    assert "-t" in argv
    assert argv[-4:] == ["tmux", "attach", "-t", "factory"]


def test_tmux_launch_is_detached_and_survives_the_factory_exiting() -> None:
    launch = build_tmux_launch("/w", "factory study /w")
    assert launch.startswith("tmux new-session -d -s factory")
    assert "exec sh -i" in launch          # a failed run stays inspectable


def test_plan_steps_are_create_then_assertions_then_run(tmp_path: Path) -> None:
    from factory.contained.provenance import provenance_probes

    probes = provenance_probes("/w", expect_factory_state=True, expect_git=True, content=None)
    steps = plan_steps(_plan(tmp_path), probes)
    assert steps[0].name == "create"
    assert steps[-1].name == "run"
    assert [s.name for s in steps[1:-1]] == [f"assert:{p.name}" for p in probes]


def test_container_name_keeps_the_hash_when_the_stem_is_long() -> None:
    from factory.podman import project_hash

    long = Path("/tmp/a-really-quite-long-project-directory-name")
    name = container_name(long)
    assert len(name) <= 32
    # The stem is what gets truncated; the hash is what keeps two same-named projects apart.
    assert name.endswith(project_hash(long)[:6])
    assert container_name(Path("/a/rta")) != container_name(Path("/b/rta"))


# --------------------------------------------------------------------------------------------
# Dry run (§12) — composes the same argv the real path runs, and provisions nothing
# --------------------------------------------------------------------------------------------


def test_dry_run_flag_is_read_from_the_environment() -> None:
    assert dry_run_enabled({"FACTORY_CONTAINED_DRY_RUN": "1"})
    assert dry_run_enabled({"FACTORY_CONTAINED_DRY_RUN": "true"})
    assert not dry_run_enabled({})
    assert not dry_run_enabled({"FACTORY_CONTAINED_DRY_RUN": "0"})


@pytest.fixture()
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "rta"
    project.mkdir()
    (project / "README.md").write_text("# rta\n")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=project, check=True,
    )
    return project


def test_dry_run_prints_the_real_steps_and_provisions_nothing(
    git_project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "contained-home"
    with patch.dict(
        os.environ,
        {"FACTORY_CONTAINED_DRY_RUN": "1", "FACTORY_CONTAINED_HOME": str(home)},
        clear=False,
    ):
        args = interpret(["--", "study", str(git_project)])
        code = cli.cmd_contained(args)
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("DRY RUN")
    assert "[create] podman run -d --init" in out
    assert "[run] podman exec" in out
    assert "tmux new-session -d -s factory" in out
    # Nothing was materialized: the workspace copy does not exist.
    assert not home.exists()


def test_dry_run_does_not_leak_a_forwarded_key(
    git_project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "contained-home"
    with patch.dict(
        os.environ,
        {
            "FACTORY_CONTAINED_DRY_RUN": "1",
            "FACTORY_CONTAINED_HOME": str(home),
            "GH_TOKEN": "ghp-supersecret",
        },
        clear=False,
    ):
        args = interpret(["--forward", "GH_TOKEN", "--", "study", str(git_project)])
        code = cli.cmd_contained(args)
    out = capsys.readouterr().out
    assert code == 0
    assert "ghp-supersecret" not in out
    assert "GH_TOKEN=<redacted>" in out


def test_forwarding_an_unset_variable_fails_before_provisioning(
    git_project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch.dict(
        os.environ,
        {"FACTORY_CONTAINED_DRY_RUN": "1", "FACTORY_CONTAINED_HOME": str(tmp_path / "h")},
        clear=False,
    ):
        os.environ.pop("DEFINITELY_NOT_SET", None)
        args = interpret(["--forward", "DEFINITELY_NOT_SET", "--", "study", str(git_project)])
        code = cli.cmd_contained(args)
    assert code == 2
    assert "DEFINITELY_NOT_SET" in capsys.readouterr().err


def test_a_payload_naming_no_project_is_rejected() -> None:
    with pytest.raises(ContainedError):
        cli._resolve_project(["ceo", "--focus", "something"])


def test_malformed_env_pair_is_rejected() -> None:
    with pytest.raises(ContainedError):
        cli._parse_extra_env(["NOT_A_PAIR"])
    assert cli._parse_extra_env(["EMPTY="]) == {"EMPTY": ""}


def test_live_is_reserved_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    args = interpret(["--live", "--", "study", "/tmp"])
    assert cli.cmd_contained(args) == 2
    assert "reserved" in capsys.readouterr().err


def test_identity_is_projected_in_dry_run_without_starting_a_probe(tmp_path: Path) -> None:
    from factory.contained.identity import resolve_identity

    with patch("factory.contained.identity.subprocess.run") as run:
        identity = resolve_identity("img", Mount(tmp_path, str(tmp_path)), dry_run=True)
    run.assert_not_called()
    assert identity == Identity(
        user=f"{os.getuid()}:0", userns=None, detail=identity.detail
    )
