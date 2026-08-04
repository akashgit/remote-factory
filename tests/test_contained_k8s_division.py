"""The cluster container-manufacturing plane: the Build path, the sidecar, and the boundary."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from factory.cli import contained as cli
from factory.cli.contained_k8s import _build_pod_plan
from factory.contained import k8s, k8s_division
from factory.contained.k8s import (
    FACTORY_CONTAINER,
    SIDECAR_CONTAINER,
    WORKSPACE_ROOT,
    PodPlan,
    render_pod,
)
from factory.contained.workspace import plan_workspace


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _plan(tmp_path: Path, *, division: bool = True) -> PodPlan:
    project = tmp_path / "rta"
    project.mkdir(exist_ok=True)
    parser = argparse.ArgumentParser(prog="factory")
    sub = parser.add_subparsers(dest="command")
    cli.build_contained_parser(sub)
    args = parser.parse_args(
        ["contained", "--target", "k8s", "--namespace", "ns",
         *(["--division"] if division else []), "--", "ceo", str(project)]
    )
    cli.interpret(cli._PARSER, args)
    with patch.dict(os.environ, {"FACTORY_CONTAINED_HOME": str(tmp_path / "home")}, clear=False):
        ws = plan_workspace(project, "rta-test")
        return _build_pod_plan(args, ws, "ns", "rta-test")


# --------------------------------------------------------------------------------------------
# The cluster division (§6)
# --------------------------------------------------------------------------------------------


def test_the_division_refuses_where_the_build_api_is_absent() -> None:
    from factory.cli.contained_k8s import _require_openshift

    with patch("factory.contained.k8s_division.openshift_available", return_value=False):
        with pytest.raises(k8s.ClusterError, match="build.openshift.io"):
            _require_openshift(dry_run=False)


def test_openshift_is_detected_by_api_not_by_the_oc_binary() -> None:
    argv = k8s.build_api_resources_argv("build.openshift.io")
    assert "api-resources" in argv
    assert "build.openshift.io" in argv
    assert k8s_division.openshift_available(lambda a: _completed("builds\nbuildconfigs")) is True
    assert k8s_division.openshift_available(lambda a: _completed("", returncode=1)) is False


def test_the_sidecar_is_a_separate_container(tmp_path: Path) -> None:
    doc = yaml.safe_load(render_pod(_plan(tmp_path, division=True)))
    names = [c["name"] for c in doc["spec"]["containers"]]
    assert names == [FACTORY_CONTAINER, SIDECAR_CONTAINER]
    sidecar = doc["spec"]["containers"][1]
    # It shares the workspace and nothing else; the agent's container has no route into it.
    assert sidecar["volumeMounts"][0]["mountPath"] == WORKSPACE_ROOT


def test_the_agent_gets_both_servers_and_the_brief(tmp_path: Path) -> None:
    plan = _plan(tmp_path, division=True)
    assert "kubernetes-mcp-server" in plan.run_command
    assert k8s_division.SERVER_PATH in plan.run_command
    assert k8s_division.DIVISION_BRIEF_PATH in plan.run_command


def test_the_cluster_credential_source_is_explicit_not_auto_detected() -> None:
    """An agent silently sitting in a needs-auth state looks identical to one with broken tools."""
    config = k8s_division.mcp_config("ns")
    kubernetes = config["mcpServers"][k8s_division.MCP_CLUSTER_SERVER]
    assert "--namespace" in kubernetes["args"] and "ns" in kubernetes["args"]
    assert "env" in kubernetes


def test_the_build_server_holds_no_credentials_and_speaks_to_no_cluster() -> None:
    source = k8s_division.start_build_server_source()
    assert "oc " not in source
    assert "kubectl" not in source
    assert "start_build" in source
    # It is a file drop onto the shared volume; the sidecar is the only thing that builds.
    assert k8s_division.REQUEST_DIR in source
    assert k8s_division.RESULT_DIR in source


def test_the_build_server_is_a_valid_python_module() -> None:
    import ast

    ast.parse(k8s_division.start_build_server_source())


def test_the_brief_tells_the_agent_it_cannot_exec_and_must_label() -> None:
    brief = k8s_division.division_files("ns", "rta-test")[k8s_division.DIVISION_BRIEF_PATH]
    assert "not things to build" in brief
    assert "cannot exec into other pods" in brief
    assert "factory.run: rta-test" in brief
    assert k8s_division.INTERNAL_REGISTRY in brief


def test_the_sweep_selects_by_the_run_label_only() -> None:
    argv = k8s_division.sweep_argv("ns", "rta-test")
    assert "delete" in argv and "pods" in argv
    assert "factory.run=rta-test" in argv
    # ImageStreams are deliberately not swept — they retain the tags the build produced.
    assert "imagestream" not in " ".join(argv)
