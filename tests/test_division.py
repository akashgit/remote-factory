"""Tests for the division — the build plane that lives outside the sandbox.

`--division` deliberately opens the isolation boundary, because OpenShell's seccomp filter makes
builds inside it impossible. These tests do not relitigate that decision; they check that the
opening is exactly as narrow as designed. Every failure mode here is silent — a bind mount one
directory too wide, an extra MCP tool, or a privileged build pod all work perfectly while making the
boundary permanently larger.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factory.division import (
    DRIVER_ENV,
    LOCAL_DIVISION_TOOLS,
    BuildPodSpec,
    DivisionError,
    audit_pod,
    bind_mount_config,
    build_local_policy,
    check_bind_mounts_enabled,
    check_division_endpoint,
    detect_compute_driver,
    mcp_client_config,
    load_pod_manifest,
    render_build_pod,
    render_policy_yaml,
    strategic_merge,
)


class TestBindMount:
    def test_source_and_target_are_the_project_path_exactly(self, tmp_path: Path) -> None:
        """image_build takes an absolute path on the host, so the path must resolve identically
        inside and outside the sandbox."""
        config = bind_mount_config("podman", tmp_path / "proj")
        mount = config["podman"]["mounts"][0]
        assert mount["source"] == mount["target"] == str(tmp_path / "proj")

    def test_scoped_to_the_project_not_its_parent(self, tmp_path: Path) -> None:
        project = tmp_path / "nested" / "proj"
        source = bind_mount_config("podman", project)["podman"]["mounts"][0]["source"]
        assert source == str(project)
        assert source != str(project.parent)
        assert source != str(Path.home())

    def test_mount_is_writable(self, tmp_path: Path) -> None:
        assert bind_mount_config("podman", tmp_path)["podman"]["mounts"][0]["read_only"] is False

    @pytest.mark.parametrize("driver", ["podman", "docker"])
    def test_top_level_key_is_the_driver(self, driver: str, tmp_path: Path) -> None:
        """A driver-config keyed by the wrong name is silently ignored, and the sandbox comes up
        with no bind mount at all."""
        assert list(bind_mount_config(driver, tmp_path)) == [driver]

    def test_unknown_driver_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(DivisionError):
            bind_mount_config("containerd", tmp_path)


class TestDriverDetection:
    @pytest.mark.parametrize("driver", ["podman", "docker"])
    def test_env_override(self, driver: str) -> None:
        assert detect_compute_driver(env={DRIVER_ENV: driver}) == driver

    def test_ignores_a_nonsense_override(self) -> None:
        assert detect_compute_driver(env={DRIVER_ENV: "containerd"}) in (None, "podman", "docker")


class TestBindMountPrecondition:
    def test_raises_when_a_local_config_says_the_setting_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gateway config that is readable and has the setting off is worth stopping for: the
        sandbox would get a copy of the project and image_build would fail on a path the host does
        not have, a long way from the cause."""
        config = tmp_path / "gateway.toml"
        config.write_text("[openshell.drivers.docker]\nenable_bind_mounts = false\n")
        monkeypatch.setattr("factory.division.GATEWAY_CONFIG_PATHS", (config,))
        with pytest.raises(DivisionError) as exc:
            check_bind_mounts_enabled()
        assert "enable_bind_mounts" in str(exc.value)

    def test_unknown_warns_rather_than_refusing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`gateway info` does not report the setting in 0.0.92, so treating unknown as off refused
        every run, including on correctly configured gateways. The gateway refuses the create by
        name if it really is off, and nothing is provisioned either way."""
        monkeypatch.setattr(
            "factory.division.GATEWAY_CONFIG_PATHS", (tmp_path / "absent.toml",)
        )
        warnings = check_bind_mounts_enabled()
        assert any("enable_bind_mounts" in w for w in warnings)

    def test_accepts_a_config_that_enables_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / "gateway.toml"
        config.write_text("[openshell.drivers.docker]\nenable_bind_mounts = true\n")
        monkeypatch.setattr("factory.division.GATEWAY_CONFIG_PATHS", (config,))
        assert check_bind_mounts_enabled() == []


class TestMcpPolicy:
    def test_allowlist_is_exactly_the_intended_tools(self) -> None:
        policy = build_local_policy()
        rules = policy["network_policies"]["factory_division"]["endpoints"][0]["rules"]
        assert [r["allow"]["tool"] for r in rules] == list(LOCAL_DIVISION_TOOLS)

    def test_every_rule_names_a_tool(self) -> None:
        """Policy cannot inspect arguments, so a rule matching a method without a tool name
        constrains nothing at all."""
        rules = build_local_policy()["network_policies"]["factory_division"]["endpoints"][0]["rules"]
        assert all(r["allow"].get("tool") for r in rules)
        assert {r["allow"]["method"] for r in rules} == {"tools/call"}

    def test_rules_use_the_schema_the_gateway_accepts(self) -> None:
        """The flat `{method, name}` form is rejected at parse time with `unknown field 'method',
        expected 'allow'`, which aborts provisioning before the sandbox exists."""
        rules = build_local_policy()["network_policies"]["factory_division"]["endpoints"][0]["rules"]
        assert all(set(r) == {"allow"} for r in rules)

    def test_policy_is_complete_not_just_the_division_rule(self, tmp_path: Path) -> None:
        """`--policy` replaces the sandbox default rather than merging with it. A file carrying
        only the division rule leaves the sandbox with no filesystem policy — verified: /usr,
        /sandbox and /dev/null all become inaccessible."""
        policy = build_local_policy(tmp_path)
        assert policy["filesystem_policy"]["read_only"]
        assert policy["process"]["run_as_user"] == "sandbox"
        assert str(tmp_path) in policy["filesystem_policy"]["read_write"]

    def test_bind_mount_target_is_writable_by_policy(self, tmp_path: Path) -> None:
        """The mount puts the directory in the container; the policy decides whether the agent may
        open it. Two different gates, and missing the second reads as a broken mount."""
        policy = build_local_policy(tmp_path / "proj")
        assert str(tmp_path / "proj") in policy["filesystem_policy"]["read_write"]

    def test_enforced_not_observed(self) -> None:
        endpoint = build_local_policy()["network_policies"]["factory_division"]["endpoints"][0]
        assert endpoint["enforcement"] == "enforce"
        assert endpoint["protocol"] == "mcp"

    def test_wildcard_is_refused(self) -> None:
        with pytest.raises(DivisionError):
            build_local_policy(tools=("image_build", "*"))

    def test_duplicate_tool_is_refused(self) -> None:
        with pytest.raises(DivisionError):
            build_local_policy(tools=("image_build", "image_build"))

    def test_renders_as_yaml(self) -> None:
        loaded = yaml.safe_load(render_policy_yaml(build_local_policy()))
        assert loaded == build_local_policy()


class TestMcpClientRegistration:
    def test_points_at_the_bridge_host(self) -> None:
        """The policy permits the tool calls but does not advertise them; without a client
        registration the agent never learns the server exists and the division goes unused."""
        config = mcp_client_config()
        server = config["mcpServers"]["podman"]
        assert server["url"] == "http://host.openshell.internal:8430/mcp"
        assert server["type"] == "http"

    def test_url_matches_the_policy_endpoint(self) -> None:
        """A registration pointing somewhere the policy does not allow is denied at the boundary,
        and reads as a network fault rather than a configuration mismatch."""
        endpoint = build_local_policy()["network_policies"]["factory_division"]["endpoints"][0]
        url = mcp_client_config()["mcpServers"]["podman"]["url"]
        assert url == f"http://{endpoint['host']}:{endpoint['port']}{endpoint['path']}"


class TestDivisionEndpointCheck:
    def test_raises_when_nothing_is_listening(self) -> None:
        """The factory does not start podman-mcp-server: it has no authentication and fronts the
        host's podman socket, so spawning it unasked is not the factory's call."""
        with pytest.raises(DivisionError) as exc:
            check_division_endpoint(port=1)
        assert "podman-mcp-server" in str(exc.value)

    def test_passes_when_something_is_listening(self) -> None:
        import socket

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            check_division_endpoint(port=server.getsockname()[1])
        finally:
            server.close()


def _pod() -> dict:
    return render_build_pod(BuildPodSpec(name="p", namespace="ns", image_ref="img:tag"))


class TestBuildPod:
    def test_asks_for_no_privilege(self) -> None:
        assert audit_pod(_pod()) == []

    def test_capabilities_are_exactly_setuid_setgid(self) -> None:
        """The buildah tutorial's verified capability set excludes cap_sys_admin, so asking for
        more would widen the grant for nothing."""
        caps = _pod()["spec"]["containers"][0]["securityContext"]["capabilities"]
        assert sorted(caps["add"]) == ["SETGID", "SETUID"]
        assert caps["drop"] == ["ALL"]

    def test_chroot_isolation(self) -> None:
        """chroot keeps buildah away from runc and therefore away from CLONE_NEWUSER, which is
        exactly what the sandbox's filter denies."""
        env = {e["name"]: e["value"] for e in _pod()["spec"]["containers"][0]["env"]}
        assert env["BUILDAH_ISOLATION"] == "chroot"

    def test_container_storage_is_an_empty_dir(self) -> None:
        """emptyDir yields native overlay diff, so overlay works without fuse-overlayfs."""
        volumes = {v["name"]: v for v in _pod()["spec"]["volumes"]}
        assert "emptyDir" in volumes["containers"]
        mounts = {m["mountPath"] for m in _pod()["spec"]["containers"][0]["volumeMounts"]}
        assert "/home/build/.local/share/containers" in mounts

    def test_subuid_and_subgid_are_visible_in_the_manifest(self) -> None:
        mounts = {m["mountPath"] for m in _pod()["spec"]["containers"][0]["volumeMounts"]}
        assert {"/etc/subuid", "/etc/subgid"} <= mounts

    def test_no_host_path_anywhere(self) -> None:
        assert "hostPath" not in yaml.safe_dump(_pod())

    def test_namespace_is_never_hardcoded(self) -> None:
        pod = render_build_pod(BuildPodSpec(name="p", namespace="probe-ns", image_ref="i"))
        assert pod["metadata"]["namespace"] == "probe-ns"
        assert "factory-division" not in yaml.safe_dump(pod)


class TestAudit:
    def test_flags_privileged(self) -> None:
        pod = _pod()
        pod["spec"]["containers"][0]["securityContext"]["privileged"] = True
        assert any("privileged" in f for f in audit_pod(pod))

    def test_flags_sys_admin(self) -> None:
        pod = _pod()
        pod["spec"]["containers"][0]["securityContext"]["capabilities"]["add"].append("SYS_ADMIN")
        assert any("SYS_ADMIN" in f for f in audit_pod(pod))

    def test_flags_host_path(self) -> None:
        pod = _pod()
        pod["spec"]["volumes"].append({"name": "h", "hostPath": {"path": "/"}})
        assert any("hostPath" in f for f in audit_pod(pod))


class TestStrategicMerge:
    def test_patches_one_field_without_replacing_the_container(self) -> None:
        base = _pod()
        patched = strategic_merge(
            base, {"spec": {"containers": [{"name": "build", "resources": {"limits": {"memory": "3Gi"}}}]}}
        )
        container = patched["spec"]["containers"][0]
        assert container["resources"] == {"limits": {"memory": "3Gi"}}
        assert container["image"] == base["spec"]["containers"][0]["image"]
        assert container["command"] == base["spec"]["containers"][0]["command"]
        assert container["securityContext"] == base["spec"]["containers"][0]["securityContext"]

    def test_guarantees_survive_a_patch(self) -> None:
        patched = strategic_merge(
            _pod(), {"spec": {"containers": [{"name": "build", "resources": {"limits": {"cpu": "2"}}}]}}
        )
        assert audit_pod(patched) == []

    def test_appends_an_unknown_named_entry(self) -> None:
        patched = strategic_merge(_pod(), {"spec": {"volumes": [{"name": "extra", "emptyDir": {}}]}})
        names = [v["name"] for v in patched["spec"]["volumes"]]
        assert names[-1] == "extra"
        assert "containers" in names

    def test_replaces_lists_without_name_keys(self) -> None:
        merged = strategic_merge({"a": [1, 2, 3]}, {"a": [9]})
        assert merged["a"] == [9]

    def test_does_not_mutate_the_base(self) -> None:
        base = _pod()
        strategic_merge(base, {"spec": {"containers": [{"name": "build", "resources": {"x": 1}}]}})
        assert "resources" not in base["spec"]["containers"][0]


class TestLoadManifest:
    def test_loads_a_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "pod.yaml"
        path.write_text("apiVersion: v1\nkind: Pod\n")
        assert load_pod_manifest(path)["kind"] == "Pod"

    def test_rejects_a_non_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "pod.yaml"
        path.write_text("- one\n- two\n")
        with pytest.raises(DivisionError):
            load_pod_manifest(path)


class TestK8sBuildObjects:
    def test_build_is_standalone_and_configmap_sourced(self) -> None:
        """A BuildConfig with a binary source would need `oc start-build --from-archive` from the
        host for every iteration, which the agent has no way to perform."""
        from factory.division import BuildObjectsSpec, render_build

        spec = BuildObjectsSpec(name="widget", namespace="ns", tag="20260101", dockerfile="FROM x\n")
        build = render_build(spec, build_name="widget-1")
        assert build["kind"] == "Build"
        assert build["spec"]["source"]["type"] == "Dockerfile"
        assert build["spec"]["source"]["configMaps"][0]["configMap"]["name"] == "factory-build-context"
        assert build["spec"]["output"]["to"]["name"] == "widget:20260101"

    def test_dockerfile_travels_in_the_build_not_the_context(self) -> None:
        """The build-fix-rebuild loop is a patch to this field; that is what makes it MCP-drivable."""
        from factory.division import BuildObjectsSpec, render_build

        spec = BuildObjectsSpec(name="w", namespace="ns", tag="t", dockerfile="FROM scratch\n")
        assert render_build(spec, build_name="b")["spec"]["source"]["dockerfile"] == "FROM scratch\n"

    def test_image_ref_points_at_the_internal_registry(self) -> None:
        from factory.division import BuildObjectsSpec, internal_image_ref

        spec = BuildObjectsSpec(name="w", namespace="ns", tag="t", dockerfile="")
        ref = internal_image_ref(spec)
        assert ref == "image-registry.openshift-image-registry.svc:5000/ns/w:t"


class TestK8sPolicy:
    def test_tools_are_the_loops_tools_only(self) -> None:
        from factory.division import K8S_DIVISION_TOOLS, build_k8s_policy

        rules = build_k8s_policy()["network_policies"]["factory_division"]["endpoints"][0]["rules"]
        assert [r["allow"]["tool"] for r in rules] == list(K8S_DIVISION_TOOLS)

    def test_pods_exec_is_not_allowlisted(self) -> None:
        """The loop never needs a shell in someone else's pod, and policy cannot constrain what one
        would run."""
        from factory.division import K8S_DIVISION_TOOLS

        assert "pods_exec" not in K8S_DIVISION_TOOLS

    def test_k8s_policy_is_a_complete_policy(self) -> None:
        from factory.division import build_k8s_policy

        policy = build_k8s_policy()
        assert policy["filesystem_policy"]["read_only"]
        assert policy["network_policies"]["factory_division"]["binaries"]

    def test_k8s_endpoint_is_its_own_port(self) -> None:
        from factory.division import K8S_DIVISION_PORT, DIVISION_PORT, build_k8s_policy

        endpoint = build_k8s_policy()["network_policies"]["factory_division"]["endpoints"][0]
        assert endpoint["port"] == K8S_DIVISION_PORT != DIVISION_PORT


class TestStdioBridge:
    def test_k8s_server_is_registered_over_stdio(self) -> None:
        """An HTTP registration makes Claude Code probe `/.well-known/...` first; inside a sandbox
        that probe is denied and the client offers `authenticate` instead of the server's tools."""
        from factory.division import mcp_client_config_k8s

        server = mcp_client_config_k8s()["mcpServers"]["kubernetes"]
        assert server["type"] == "stdio"
        assert server["command"] == "python3"

    def test_bridge_is_told_where_to_forward(self) -> None:
        from factory.division import K8S_DIVISION_PORT, mcp_client_config_k8s

        env = mcp_client_config_k8s()["mcpServers"]["kubernetes"]["env"]
        assert env["FACTORY_MCP_BRIDGE_URL"].endswith(f":{K8S_DIVISION_PORT}/mcp")
        # kubernetes-mcp-server rejects the bridge hostname with `invalid Host header`.
        assert env["FACTORY_MCP_BRIDGE_HOST_HEADER"] == f"localhost:{K8S_DIVISION_PORT}"

    def test_bridge_source_is_stdlib_only(self) -> None:
        """The sandbox's network policy has no rule that would let pip run."""
        from factory.division import mcp_bridge_source

        source = mcp_bridge_source()
        imports = [ln.split()[1] for ln in source.splitlines() if ln.startswith("import ")]
        assert set(imports) <= {"json", "os", "sys", "urllib.error", "urllib.request"}

    def test_bridge_interpreters_are_allowlisted(self) -> None:
        """A policy with no matching binary denies everything, and the bridge is what makes the
        calls now — not claude or node."""
        from factory.division import BRIDGE_INTERPRETERS, build_k8s_policy

        paths = {
            b["path"] for b in build_k8s_policy()["network_policies"]["factory_division"]["binaries"]
        }
        assert set(BRIDGE_INTERPRETERS) <= paths


class TestDivisionBrief:
    def test_brief_names_the_tools_and_forbids_wrapping_them(self) -> None:
        """Registering the server advertises the tools but says nothing about what they are for.
        Left to infer it, a Refiner scoped 165 lines across three files to build a CLI command that
        would wrap them — in the same breath as "do not modify any source file"."""
        from factory.division import division_brief

        local = division_brief("local")
        assert "mcp__podman__image_build" in local
        assert "not a feature to implement" in local

        k8s = division_brief("k8s", manifest=".factory/division/build.yaml", image_ref="reg/x:1")
        assert "mcp__kubernetes__resources_create_or_update" in k8s
        assert ".factory/division/build.yaml" in k8s
        assert "reg/x:1" in k8s
        assert "not a feature to implement" in k8s

    def test_k8s_brief_describes_the_failure_loop(self) -> None:
        """The loop is the point of the division; a brief that only covers the happy path leaves
        the agent with no idea what to do with a failed build."""
        from factory.division import division_brief

        brief = division_brief("k8s")
        assert "pods_log" in brief
        assert "events_list" in brief
        assert "spec.source.dockerfile" in brief
