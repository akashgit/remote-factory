"""Tests for `factory contained` — command composition, transfer planning, and the guards.

The behaviors here fail quietly rather than loudly, which is why each has a test. A dropped
`.factory/` transfer looks exactly like a fresh project. A forwarded credential looks exactly like
a working sandbox. A `--division` that switched itself on looks exactly like one you asked for.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from factory.cli._run_args import SANDBOX_ENV_POLICY, redact_argv, redact_env
from factory.cli.contained import _build_plan, _check_scope, build_contained_parser, cmd_contained
from factory.openshell import (
    DRY_RUN_ENV,
    LABEL_PROJECT,
    build_create_command,
    build_list_command,
    build_run_command,
    build_upload_commands,
    growth_context_warning,
    plan_steps,
    project_hash,
    sandbox_name,
)


@pytest.fixture()
def gitignored_project(tmp_path: Path) -> Path:
    """A project whose .gitignore lists .factory/ — the shape that triggers the transfer trap."""
    project = tmp_path / "widget"
    (project / ".factory").mkdir(parents=True)
    (project / ".gitignore").write_text(".factory/\n")
    (project / ".factory" / "config.json").write_text('{"name": "widget"}\n')
    (project / ".factory" / "results.tsv").write_text("1\ta\n2\tb\n3\tc\n")
    return project


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command")
    build_contained_parser(sub)
    return root


def _args(project: Path, **overrides: object) -> argparse.Namespace:
    parsed = _parser().parse_args(["contained", str(project)])
    for key, value in overrides.items():
        setattr(parsed, key, value)
    return parsed


class TestParserGuards:
    def test_tmux_persist_is_rejected_while_parsing(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            _parser().parse_args(["contained", "/tmp/x", "--tmux-persist"])
        assert exc.value.code == 2
        assert "tmux" in capsys.readouterr().err.lower()

    def test_division_requires_a_value(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["contained", "/tmp/x", "--division"])

    def test_division_defaults_to_unset(self) -> None:
        assert _parser().parse_args(["contained", "/tmp/x"]).division is None

    def test_division_is_not_inherited_from_target(self) -> None:
        parsed = _parser().parse_args(["contained", "/tmp/x", "--target", "k8s"])
        assert parsed.division is None

    def test_rejects_unknown_division_value(self) -> None:
        with pytest.raises(SystemExit):
            _parser().parse_args(["contained", "/tmp/x", "--division", "swarm"])


class TestScopeChecks:
    def test_local_target_without_division_is_in_scope(self, tmp_path: Path) -> None:
        assert _check_scope(_args(tmp_path)) is None

    def test_k8s_division_needs_a_namespace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The namespace is never defaulted. `default` exists on every cluster, and creating build
        objects there because the context could not be read is a mistake someone else finds."""
        monkeypatch.setattr("factory.cli.contained.current_namespace", lambda: None)
        error = _check_scope(_args(tmp_path, division="k8s"))
        assert error is not None
        assert "--namespace" in error

    def test_k8s_division_accepts_the_contexts_namespace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("factory.cli.contained.current_namespace", lambda: "factory-division")
        assert _check_scope(_args(tmp_path, division="k8s")) is None

    def test_local_division_is_in_scope(self, tmp_path: Path) -> None:
        """Implemented since phase 3. Its gateway preconditions are checked later, and raise
        DivisionError rather than being a scope rejection."""
        assert _check_scope(_args(tmp_path, division="local")) is None

    def test_dry_run_exempts_the_k8s_rejections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run renders the build pod so it can be reviewed without a cluster; nothing is
        provisioned either way, so refusing to render would only make it harder to inspect."""
        monkeypatch.setenv(DRY_RUN_ENV, "1")
        assert _check_scope(_args(tmp_path, division="k8s")) is None
        assert _check_scope(_args(tmp_path, target="k8s")) is None


class TestGrowthContextWarning:
    def test_warns_and_names_both_when_absent(self) -> None:
        warning = growth_context_warning({})
        assert warning is not None
        assert "FACTORY_MANAGED_DIRS" in warning
        assert "FACTORY_VAULT_PATH" in warning

    def test_names_only_the_missing_one(self) -> None:
        warning = growth_context_warning({"FACTORY_MANAGED_DIRS": "/m"})
        assert warning is not None
        assert "FACTORY_VAULT_PATH" in warning
        assert "FACTORY_MANAGED_DIRS" not in warning

    def test_silent_when_both_present(self) -> None:
        assert growth_context_warning({"FACTORY_MANAGED_DIRS": "/m", "FACTORY_VAULT_PATH": "/v"}) is None

    def test_blank_value_counts_as_absent(self) -> None:
        assert growth_context_warning({"FACTORY_MANAGED_DIRS": "  ", "FACTORY_VAULT_PATH": "/v"}) is not None


class TestTransferPlan:
    def test_factory_state_is_a_separate_upload_bypassing_gitignore(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        by_local = {u.local: u for u in plan.uploads}
        state = by_local[gitignored_project / ".factory"]
        assert state.respect_gitignore is False
        # `dest` is the parent directory the upload lands *inside*, so the state upload names the
        # project directory and arrives as `.factory` within it.
        assert state.dest == plan.sandbox_path
        assert by_local[gitignored_project].respect_gitignore is True

    def test_gitignore_bypassing_uploads_are_not_folded_into_create(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--no-git-ignore is per-command, so folding it into create would also drag in build
        artifacts, virtualenvs, and caches the project deliberately ignores."""
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        create = build_create_command(plan)
        assert "--no-git-ignore" not in create
        uploads = build_upload_commands(plan)
        assert uploads and all("--no-git-ignore" in cmd for cmd in uploads)
        assert any(str(gitignored_project / ".factory") in cmd for cmd in uploads)

    def test_transfer_is_verified_not_trusted(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        steps = {s.name: s for s in plan_steps(plan)}
        assert "assert_factory_state" in steps
        assert f"{plan.sandbox_path}/.factory/config.json" in steps["assert_factory_state"].argv

    def test_missing_factory_dir_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.environ", {})
        bare = tmp_path / "bare"
        bare.mkdir()
        plan = _build_plan(_args(bare), bare)
        assert any(".factory/" in w for w in plan.warnings)

    def test_upload_order_puts_state_after_the_tree(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tree upload creates the directory the state upload writes into."""
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        locals_ = [u.local for u in plan.uploads]
        assert locals_.index(gitignored_project) < locals_.index(gitignored_project / ".factory")


class TestSandboxIdentity:
    def test_tracked_by_label_not_a_mapping_file(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        assert plan.labels[LABEL_PROJECT] == project_hash(gitignored_project)
        listing = build_list_command(gitignored_project)
        assert "--selector" in listing
        assert f"{LABEL_PROJECT}={project_hash(gitignored_project)}" in listing

    def test_name_is_stable_and_path_specific(self, tmp_path: Path) -> None:
        a, b = tmp_path / "one" / "app", tmp_path / "two" / "app"
        assert sandbox_name(a) == sandbox_name(a)
        assert sandbox_name(a) != sandbox_name(b)


class TestComposedEnvironment:
    HOSTILE_ENV = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": "us-east5",
        "ANTHROPIC_API_KEY": "sk-ant-real",
        "OPENAI_API_KEY": "sk-openai-real",
        "CODEX_API_KEY": "codex-real",
        "BOBSHELL_API_KEY": "bob-real",
        "FACTORY_MODEL": "opus",
        DRY_RUN_ENV: "1",
        "FACTORY_EVAL_CASE": "probe",
    }

    def test_no_credential_reaches_the_sandbox(self) -> None:
        """Credentials belong on the gateway (spec §8). A forwarded key would land in the sandbox
        argv, and from there into any dry-run output or retained evidence file."""
        resolved = SANDBOX_ENV_POLICY.resolve(self.HOSTILE_ENV)
        assert "OPENAI_API_KEY" not in resolved
        assert "CODEX_API_KEY" not in resolved
        assert "BOBSHELL_API_KEY" not in resolved
        assert resolved["ANTHROPIC_API_KEY"] == "unused"
        assert not any("real" in v for v in resolved.values())

    def test_dry_run_flag_does_not_cross(self) -> None:
        """Dry-run is a decision about this invocation; forwarding it would put the sandboxed
        factory into dry-run too, so it would compose commands and run nothing."""
        assert DRY_RUN_ENV not in SANDBOX_ENV_POLICY.resolve(self.HOSTILE_ENV)

    def test_probe_plumbing_does_not_cross(self) -> None:
        assert "FACTORY_EVAL_CASE" not in SANDBOX_ENV_POLICY.resolve(self.HOSTILE_ENV)

    def test_ordinary_factory_config_still_crosses(self) -> None:
        assert SANDBOX_ENV_POLICY.resolve(self.HOSTILE_ENV)["FACTORY_MODEL"] == "opus"


class TestRedaction:
    def test_masks_forwarded_secrets_but_not_pinned_placeholder(self) -> None:
        env = {"ANTHROPIC_API_KEY": "unused", "FACTORY_GH_TOKEN": "ghp_real", "FACTORY_MODEL": "opus"}
        redacted = redact_env(env, SANDBOX_ENV_POLICY)
        assert redacted["ANTHROPIC_API_KEY"] == "unused"
        assert redacted["FACTORY_GH_TOKEN"] == "<redacted>"
        assert redacted["FACTORY_MODEL"] == "opus"

    def test_masks_env_pairs_in_argv(self) -> None:
        argv = ["openshell", "--env", "FACTORY_SECRET=hunter2", "--env", "FACTORY_MODEL=opus"]
        assert redact_argv(argv, SANDBOX_ENV_POLICY) == [
            "openshell",
            "--env",
            "FACTORY_SECRET=<redacted>",
            "--env",
            "FACTORY_MODEL=opus",
        ]

    def test_leaves_non_env_tokens_alone(self) -> None:
        argv = ["openshell", "sandbox", "create", "--name", "factory-key-abc"]
        assert redact_argv(argv, SANDBOX_ENV_POLICY) == argv


class TestRunCommand:
    def test_stages_user_config_inside_the_sandbox(self) -> None:
        """$HOME belongs to the image's identity, so the copy has to happen where it can expand."""
        composed = build_run_command("/sandbox/app", "factory ceo /sandbox/app", stage_user_config=True)
        assert '"$HOME/.factory"' in composed
        assert composed.endswith("exec factory ceo /sandbox/app")

    def test_omits_the_stage_when_there_is_nothing_to_stage(self) -> None:
        composed = build_run_command("/sandbox/app", "factory ceo /sandbox/app", stage_user_config=False)
        assert "HOME" not in composed


class TestDryRun:
    def test_emits_json_and_provisions_nothing(
        self,
        gitignored_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(DRY_RUN_ENV, "1")

        def _fail(*_a: object, **_k: object) -> None:
            raise AssertionError("dry-run must not execute anything")

        monkeypatch.setattr("subprocess.run", _fail)
        code = cmd_contained(_args(gitignored_project))
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert [s["step"] for s in payload["steps"]][0] == "create"

    def test_missing_growth_context_warns_without_failing(
        self,
        gitignored_project: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        assert cmd_contained(_args(gitignored_project)) == 0
        assert "Growth context not configured" in capsys.readouterr().err


class TestProvisioningSequence:
    """The ordering and argument forms that were wrong against a live gateway."""

    def test_run_happens_after_the_transfers(self, gitignored_project: Path) -> None:
        """`sandbox create` blocks until its command exits. Starting the factory as the create
        command means the uploads that follow never run — create is still streaming the factory's
        output at the moment the sandbox needs .factory/ to already be there."""
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        order = [s.name for s in plan_steps(plan)]
        assert order.index("run") > order.index("upload")
        assert order.index("run") > order.index("assert_factory_state")

    def test_create_does_not_carry_the_factory_command(self, gitignored_project: Path) -> None:
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        create = next(s for s in plan_steps(plan) if s.name == "create")
        assert "factory ceo" not in " ".join(create.argv)

    def test_run_uses_exec_with_a_named_sandbox(self, gitignored_project: Path) -> None:
        """`openshell sandbox exec <name> -- ...` parses <name> as the first word of the command
        and silently targets the last-used sandbox instead."""
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        run = next(s for s in plan_steps(plan) if s.name == "run")
        assert run.argv[:4] == ["openshell", "sandbox", "exec", "--name"]
        assert run.argv[4] == plan.name
        assert "factory ceo" in run.argv[-1]

    def test_assert_step_targets_the_transferred_file(self, gitignored_project: Path) -> None:
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        step = next(s for s in plan_steps(plan) if s.name == "assert_factory_state")
        assert step.argv[:4] == ["openshell", "sandbox", "exec", "--name"]
        assert step.argv[-1].endswith("/.factory/config.json")

    def test_no_assert_when_there_was_nothing_to_transfer(self, tmp_path: Path) -> None:
        """The check exists to catch a transfer that silently dropped state that did exist.
        Running it on a legitimately fresh project reports the .gitignore trap for a project that
        never had a .factory/."""
        bare = tmp_path / "bare"
        bare.mkdir()
        plan = _build_plan(_args(bare), bare)
        assert "assert_factory_state" not in [s.name for s in plan_steps(plan)]


class TestSandboxNameLimit:
    def test_name_fits_the_gateways_limit(self, tmp_path: Path) -> None:
        """The gateway rejects longer names at create time: `name exceeds maximum length
        (26 > 19)`, after the plan is built and before anything exists."""
        from factory.openshell import MAX_SANDBOX_NAME

        for leaf in ("its_harness", "remote-factory", "a", "A_Very_Long_Project_Name_Here"):
            project = tmp_path / leaf
            project.mkdir()
            assert len(sandbox_name(project)) <= MAX_SANDBOX_NAME

    def test_the_hash_survives_truncation(self, tmp_path: Path) -> None:
        """Two same-named projects in different directories must not collide, so the readable stem
        is what gets truncated and never the digest."""
        one = tmp_path / "aaa" / "A_Very_Long_Project_Name_Here"
        two = tmp_path / "bbb" / "A_Very_Long_Project_Name_Here"
        one.mkdir(parents=True)
        two.mkdir(parents=True)
        assert sandbox_name(one) != sandbox_name(two)


class TestExtraEnv:
    def test_extra_env_reaches_the_sandbox(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.environ", {})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project, extra_env=["MAX_THINKING_TOKENS=0"]), project)
        assert plan.env["MAX_THINKING_TOKENS"] == "0"

    def test_extra_env_does_not_displace_the_pinned_inference_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project, extra_env=["FOO=bar"]), project)
        assert plan.env["ANTHROPIC_BASE_URL"] == "https://inference.local"

    def test_malformed_extra_env_is_rejected(self, tmp_path: Path) -> None:
        from factory.division import DivisionError

        project = tmp_path / "p"
        project.mkdir()
        with pytest.raises(DivisionError):
            _build_plan(_args(project, extra_env=["NOT_A_PAIR"]), project)


class TestLocalDivisionWorkspace:
    def test_project_is_worked_on_at_its_host_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """image_build resolves containerFile on the host, so a Containerfile written under
        /sandbox/<name> names a path the host does not have."""
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project, division="local"), project)
        assert plan.sandbox_path == str(project)
        assert f"cd {project}" in plan.run_command

    def test_bind_mounted_project_is_not_also_uploaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        project = tmp_path / "p"
        (project / ".factory").mkdir(parents=True)
        plan = _build_plan(_args(project, division="local"), project)
        assert all(u.local != project for u in plan.uploads)


class TestUploadDestinationSemantics:
    """`--upload LOCAL:DEST` places LOCAL *inside* DEST under its own basename."""

    def test_tree_is_uploaded_into_the_workspace_not_its_own_path(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Naming the full destination lands the tree at /sandbox/<name>/<name>, where nothing that
        later cds to /sandbox/<name> can see it — verified against a live gateway."""
        from factory.openshell import SANDBOX_WORKSPACE

        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        tree = next(u for u in plan.uploads if u.local == gitignored_project)
        assert tree.dest == SANDBOX_WORKSPACE
        assert f"{SANDBOX_WORKSPACE}/{gitignored_project.name}" == plan.sandbox_path

    def test_user_config_stage_is_addressed_by_its_parent(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from factory.openshell import USER_CONFIG_STAGE

        monkeypatch.setattr("os.environ", {})
        home_factory = tmp_path / "home" / ".factory"
        home_factory.mkdir(parents=True)
        monkeypatch.setattr(Path, "expanduser", lambda self: home_factory if str(self) == "~/.factory" else self)
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        stage = next((u for u in plan.uploads if u.local == home_factory), None)
        assert stage is not None
        assert stage.dest == str(Path(USER_CONFIG_STAGE).parent)


class TestGitMetadataTransfer:
    def test_git_dir_is_transferred_separately(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`.git` does not survive the filtered upload. Without it the CEO detects `no_repo`, falls
        back to build mode, and rejects improve-mode flags — a symptom that names the flag rather
        than the missing directory."""
        monkeypatch.setattr("os.environ", {})
        (gitignored_project / ".git").mkdir()
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        git = next(u for u in plan.uploads if u.local == gitignored_project / ".git")
        assert git.respect_gitignore is False
        assert git.dest == plan.sandbox_path

    def test_missing_git_dir_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.environ", {})
        bare = tmp_path / "bare"
        bare.mkdir()
        plan = _build_plan(_args(bare), bare)
        assert any(".git/" in w for w in plan.warnings)

    def test_no_git_upload_when_bind_mounted(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        (gitignored_project / ".git").mkdir()
        plan = _build_plan(_args(gitignored_project, division="local"), gitignored_project)
        assert plan.uploads == () or all(u.local != gitignored_project / ".git" for u in plan.uploads)


class TestHeadlessInTheSandbox:
    def test_in_sandbox_invocation_is_headless(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sandbox is driven over a pipe with no terminal. An interactive CEO session there
        produces no visible output at all while real agents run."""
        monkeypatch.setattr("os.environ", {})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project), project)
        assert "--headless" in plan.run_command

    def test_design_mode_is_rejected(self, tmp_path: Path) -> None:
        error = _check_scope(_args(tmp_path, mode="design"))
        assert error is not None
        assert "design" in error

    def test_design_mode_is_rejected_under_dry_run_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dry-run exempts the not-yet-built k8s combinations, but not this one: the mode is wrong
        under contained whether or not anything gets provisioned."""
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        assert _check_scope(_args(tmp_path, mode="design")) is not None


class TestBindMountWritability:
    def test_bind_mounted_run_probes_writability(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The mount carries the host's ownership through unchanged, so the sandbox identity can
        see the project and still not write it — and the division needs to write it."""
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project, division="local"), project)
        step = next(s for s in plan_steps(plan) if s.name == "probe_writable")
        assert step.argv[:4] == ["openshell", "sandbox", "exec", "--name"]
        assert step.argv[-2:] == ["-w", str(project)]

    def test_probe_runs_before_the_factory_starts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.environ", {DRY_RUN_ENV: "1"})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project, division="local"), project)
        order = [s.name for s in plan_steps(plan)]
        assert order.index("probe_writable") < order.index("run")

    def test_no_probe_without_a_bind_mount(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("os.environ", {})
        project = tmp_path / "p"
        project.mkdir()
        plan = _build_plan(_args(project), project)
        assert "probe_writable" not in [s.name for s in plan_steps(plan)]


class TestK8sBuildContext:
    def test_wheel_payload_is_copied_before_install(self) -> None:
        """ConfigMap files land in the build *context*, not the image; without the COPY, pip fails
        with `No such file or directory` on a path that is plainly in the context directory."""
        from factory.cli.contained import _default_dockerfile

        dockerfile = _default_dockerfile("remote_factory-0.2.0-py3-none-any.whl")
        copy_line = "COPY remote_factory-0.2.0-py3-none-any.whl ."
        assert copy_line in dockerfile
        assert dockerfile.index(copy_line) < dockerfile.index("pip install")

    def test_tarball_payload_is_added_not_copied(self) -> None:
        """ADD unpacks a .tar.gz; COPY would leave the archive sitting there."""
        from factory.cli.contained import _default_dockerfile

        dockerfile = _default_dockerfile("context.tar.gz")
        assert "ADD context.tar.gz ." in dockerfile
        assert "pip install --no-cache-dir ." in dockerfile

    def test_dockerfile_drops_back_to_the_unprivileged_user(self) -> None:
        from factory.cli.contained import _default_dockerfile

        dockerfile = _default_dockerfile("x.whl")
        assert dockerfile.rstrip().splitlines()[-3:] == [
            "USER 1001",
            'ENTRYPOINT ["factory"]',
            'CMD ["--help"]',
        ]

    def test_context_over_the_configmap_ceiling_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """etcd caps an object at ~1MiB and a ConfigMap stores its payload base64-encoded. The
        API's own refusal names a byte count and not the reason."""
        from factory.cli.contained import _k8s_bootstrap
        from factory.division import BuildObjectsSpec, DivisionError

        oversized = tmp_path / "big.whl"
        oversized.write_bytes(b"0" * 2_000_000)
        monkeypatch.setattr(
            "factory.cli.contained._k8s_context_payload", lambda _p: (oversized, oversized.name)
        )
        spec = BuildObjectsSpec(name="w", namespace="ns", tag="t", dockerfile="")
        with pytest.raises(DivisionError) as exc:
            _k8s_bootstrap(spec, tmp_path)
        assert "ConfigMap" in str(exc.value)


class TestAssertOnlyWhatExisted:
    def test_no_assert_when_config_json_was_never_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `.factory/` with partial state but no config.json is a project that was never
        initialised. Asserting there reports a file that never existed as the .gitignore trap, and
        sends the reader looking at uploads instead of at the project."""
        monkeypatch.setattr("os.environ", {})
        project = tmp_path / "p"
        (project / ".factory" / "reviews").mkdir(parents=True)
        (project / ".factory" / "events.jsonl").write_text("{}\n")
        plan = _build_plan(_args(project), project)
        assert "assert_factory_state" not in [s.name for s in plan_steps(plan)]

    def test_assert_when_config_json_is_there_to_lose(
        self, gitignored_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("os.environ", {})
        plan = _build_plan(_args(gitignored_project), gitignored_project)
        assert "assert_factory_state" in [s.name for s in plan_steps(plan)]
