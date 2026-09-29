"""Behavioral smoke tests for all registered workflows.

Validates that every registered workflow passes structural and semantic
validation, and that simple workflows can execute through the real
WorkflowExecutor with a FakeAgent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.testing import DummyTask, FakeAgent
from factory.workflow.definitions import _get_builtin_registry
from factory.workflow.primitives import AgentNode, Workflow
from factory.workflow.validation import validate_workflow


# ── Validation smoke tests ──────────────────────────────────────


class TestRegisteredWorkflowValidation:
    """Every registered workflow must pass validate_workflow() with no issues."""

    @pytest.fixture(params=list(_get_builtin_registry().keys()))
    def workflow_entry(self, request: pytest.FixtureRequest) -> tuple[str, Workflow]:
        """Parametrize over all registered workflows."""
        name = request.param
        registry = _get_builtin_registry()
        wf = registry[name]()
        return name, wf

    def test_workflow_validates_clean(
        self, workflow_entry: tuple[str, Workflow],
    ) -> None:
        """Workflow passes all structural and semantic validation checks."""
        name, wf = workflow_entry
        issues = validate_workflow(wf)
        assert not issues, (
            f"Workflow '{name}' has validation issues:\n"
            + "\n".join(f"  - {i}" for i in issues)
        )

    def test_workflow_has_valid_start_node(
        self, workflow_entry: tuple[str, Workflow],
    ) -> None:
        """Workflow start_node exists in the nodes dict."""
        name, wf = workflow_entry
        assert wf.start_node in wf.nodes, (
            f"Workflow '{name}': start_node '{wf.start_node}' not in nodes"
        )

    def test_agent_nodes_have_prompts(
        self, workflow_entry: tuple[str, Workflow],
    ) -> None:
        """Every AgentNode has a non-empty prompt_template."""
        name, wf = workflow_entry
        for nid, node in wf.nodes.items():
            if isinstance(node, AgentNode):
                assert node.prompt_template.strip(), (
                    f"Workflow '{name}': AgentNode '{nid}' has empty prompt_template"
                )


# ── Behavioral smoke test with FakeAgent ─────────────────────────


class TestFakeAgentBehavioral:
    """Run a simple workflow through the real executor with FakeAgent."""

    async def test_simple_workflow_with_fake_agent(self, tmp_path: Path) -> None:
        """A minimal AgentNode workflow executes to completion with FakeAgent."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole

        wf = Workflow(
            name="fake_agent_smoke",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build the thing.",
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.success, f"Execution failed: {result.halt_reason}"
        assert result.nodes_executed == 1
        assert agent.call_count == 1
        agent.assert_called("builder")

        # FakeAgent should have written the declared writes path
        assert (tmp_path / ".factory" / "reviews" / "builder-latest.md").exists()

    async def test_fake_agent_records_node_id(self, tmp_path: Path) -> None:
        """FakeAgent records node_id from the executor's kwarg."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole

        wf = Workflow(
            name="node_id_smoke",
            nodes={
                "researcher": AgentNode(
                    id="researcher",
                    role=AgentRole.RESEARCHER,
                    prompt_template="Research the topic.",
                    writes={".factory/reviews/research.md"},
                ),
            },
            edges=[],
            start_node="researcher",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        await executor.execute()

        assert agent.calls[0].node_id == "researcher"

    async def test_fake_agent_violate_writes(self, tmp_path: Path) -> None:
        """FakeAgent with violate_writes=True skips writing files.

        The executor is constructed with auto_write_outputs=False so it
        skips its own stdout-to-node.writes loop.  Combined with
        violate_writes=True on FakeAgent, no files are produced →
        _actual_writes() returns empty → completed_files does NOT contain
        the declared write path.
        """
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole

        wf = Workflow(
            name="violate_smoke",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf, violate_writes=True)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.success
        assert agent.call_count == 1
        # With auto_write_outputs=False + violate_writes, the file should NOT exist
        assert not (tmp_path / ".factory/reviews/builder-latest.md").exists(), (
            "violate_writes=True with auto_write_outputs=False should leave files unwritten"
        )
        assert ".factory/reviews/builder-latest.md" not in result.completed_files


# ── completed_files disk-existence verification ─────────────────


class TestCompletedFilesReflectsDisk:
    """completed_files must only contain files that actually exist on disk.

    Regression tests for the design gap where ``completed_files |= node.writes``
    unconditionally trusted declared writes.  With the ``_actual_writes()`` fix,
    the executor checks disk state so that ``_wait_for_reads`` correctly blocks
    downstream nodes whose inputs were never produced.

    Note: when the executor is constructed with ``auto_write_outputs=False``
    (e.g. for tests using ``FakeAgent``), ``_run_agent`` skips its
    stdout-to-writes loop, so the agent controls which files actually
    appear on disk.  With the default ``auto_write_outputs=True``,
    ``_run_agent`` auto-writes stdout to all declared ``writes`` paths.
    """

    async def test_missing_writes_blocks_downstream_reads(self, tmp_path: Path) -> None:
        """When an upstream node doesn't create its declared writes, downstream reads fail."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import Edge, FnNode

        wf = Workflow(
            name="missing_writes_test",
            nodes={
                "producer": FnNode(
                    id="producer",
                    command="echo done",
                    writes={"output.txt"},
                ),
                "consumer": FnNode(
                    id="consumer",
                    command="echo consumed",
                    reads={"output.txt"},
                ),
            },
            edges=[Edge(source="producer", target="consumer")],
            start_node="producer",
        )

        executor = WorkflowExecutor(wf, tmp_path, validate=False)

        # Patch _wait_for_reads to fail immediately instead of waiting 60s.
        # In this sequential flow the completed_files set is final by the
        # time the consumer is reached, so polling cannot help.
        async def _fast_wait(node: object) -> None:
            n = node  # type: ignore[assignment]
            if not n.reads:
                return
            missing = n.reads - executor.completed_files
            if missing:
                executor.result.halted = True
                executor.result.halt_reason = (
                    f"node '{n.id}' timed out waiting for reads: {sorted(missing)}"
                )

        executor._wait_for_reads = _fast_wait  # type: ignore[assignment]
        result = await executor.execute()

        # producer's "echo done" does NOT create output.txt on disk.
        # _actual_writes() sees it missing → output.txt is NOT in completed_files.
        # consumer's _wait_for_reads times out → execution halts.
        assert result.halted, "Execution should halt when declared writes don't exist on disk"
        assert "output.txt" in result.halt_reason
        assert "output.txt" not in result.completed_files

    async def test_present_writes_satisfy_downstream_reads(self, tmp_path: Path) -> None:
        """When an upstream node creates its declared writes, downstream reads succeed."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, Edge

        wf = Workflow(
            name="present_writes_test",
            nodes={
                "producer": AgentNode(
                    id="producer",
                    role=AgentRole.BUILDER,
                    prompt_template="Write the output.",
                    writes={"output.txt"},
                ),
                "consumer": AgentNode(
                    id="consumer",
                    role=AgentRole.BUILDER,
                    prompt_template="Consume the output.",
                    reads={"output.txt"},
                    writes={".factory/reviews/builder-latest.md"},
                ),
            },
            edges=[Edge(source="producer", target="consumer")],
            start_node="producer",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        # FakeAgent (violate_writes=False) writes output.txt itself.
        # auto_write_outputs=False means executor defers to FakeAgent.
        # _actual_writes() sees it → output.txt IS in completed_files.
        # consumer's _wait_for_reads is satisfied → execution succeeds.
        assert result.success, f"Execution should succeed: {result.halt_reason}"
        assert "output.txt" in result.completed_files
        assert agent.call_count == 2

    async def test_fake_agent_violate_writes_breaks_downstream(self, tmp_path: Path) -> None:
        """FakeAgent(violate_writes=True) → executor skips writes → downstream halts.

        Full chain:
        1. node_a uses FakeAgent(violate_writes=True) → FakeAgent doesn't write
        2. Executor has auto_write_outputs=False → skips stdout-to-writes
        3. _actual_writes() returns empty → output.txt NOT in completed_files
        4. node_b reads={'output.txt'} → _wait_for_reads finds it missing → halts
        """
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, Edge

        wf = Workflow(
            name="violate_writes_downstream_test",
            nodes={
                "node_a": AgentNode(
                    id="node_a",
                    role=AgentRole.BUILDER,
                    prompt_template="Produce output.",
                    writes={"output.txt"},
                ),
                "node_b": AgentNode(
                    id="node_b",
                    role=AgentRole.BUILDER,
                    prompt_template="Consume output.",
                    reads={"output.txt"},
                    writes={"final.txt"},
                ),
            },
            edges=[Edge(source="node_a", target="node_b")],
            start_node="node_a",
        )

        agent = FakeAgent(wf, violate_writes=True)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)

        # Patch _wait_for_reads to fail immediately instead of polling 60s
        async def _fast_wait(node: object) -> None:
            n = node  # type: ignore[assignment]
            if not n.reads:
                return
            missing = n.reads - executor.completed_files
            if missing:
                executor.result.halted = True
                executor.result.halt_reason = (
                    f"node '{n.id}' timed out waiting for reads: {sorted(missing)}"
                )

        executor._wait_for_reads = _fast_wait  # type: ignore[assignment]
        result = await executor.execute()

        # node_a: FakeAgent(violate_writes=True) doesn't write, executor
        # has auto_write_outputs=False → output.txt never created.
        assert not (tmp_path / "output.txt").exists(), (
            "output.txt should not exist when violate_writes=True"
        )
        assert "output.txt" not in result.completed_files

        # node_b: _wait_for_reads sees output.txt missing → halts
        assert result.halted, "Execution should halt when upstream violates writes"
        assert "output.txt" in result.halt_reason


# ── DummyTask smoke test ─────────────────────────────────────────


class TestDummyTask:
    """DummyTask exercises the real Task protocol with controlled data."""

    def test_dummy_task_defaults(self) -> None:
        """DummyTask yields 3 items and always passes verify."""
        task = DummyTask()
        instances = list(task.instances())
        assert len(instances) == 3
        assert [i.id for i in instances] == ["item-1", "item-2", "item-3"]

        for inst in instances:
            assert task.prompt(inst) == f"Process instance {inst.id}"
            result = task.verify(inst, Path("/tmp"))
            assert result.passed
            assert result.score == 1.0

    def test_dummy_task_custom_instances(self) -> None:
        """DummyTask accepts custom instances."""
        task = DummyTask(instances_data=[{"id": "x"}, {"id": "y"}])
        instances = list(task.instances())
        assert len(instances) == 2
        assert [i.id for i in instances] == ["x", "y"]

    def test_dummy_task_split_parameter(self) -> None:
        """DummyTask.instances(split=...) returns same instances for any split."""
        task = DummyTask()
        all_ids = [i.id for i in task.instances()]
        train_ids = [i.id for i in task.instances(split="train")]
        val_ids = [i.id for i in task.instances(split="val")]
        assert train_ids == all_ids
        assert val_ids == all_ids

    def test_dummy_task_setup_noop(self, tmp_path: Path) -> None:
        """DummyTask.setup() is a no-op — doesn't create any files."""
        from factory.task import TaskInstance

        task = DummyTask()
        inst = TaskInstance(id="test")
        task.setup(inst, tmp_path)
        # No files should be created by setup
        assert list(tmp_path.iterdir()) == []


# ── Post-checks enforcement ──────────────────────────────────────


class TestPostChecksEnforcement:
    """Executor enforces SPEC §7.3 post_checks on AgentNodes."""

    async def test_post_check_must_exist_fails(self, tmp_path: Path) -> None:
        """Post-check with must_exist=True fails when artifact is missing."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, ArtifactCheck

        wf = Workflow(
            name="postcheck_test",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                    post_checks=[
                        ArtifactCheck(path="nonexistent.txt", must_exist=True),
                    ],
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.halted
        assert "nonexistent.txt" in result.halt_reason
        assert "must exist" in result.halt_reason

    async def test_post_check_min_size_fails(self, tmp_path: Path) -> None:
        """Post-check with min_size fails when artifact is too small."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, ArtifactCheck

        wf = Workflow(
            name="postcheck_size",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                    post_checks=[
                        ArtifactCheck(
                            path=".factory/reviews/builder-latest.md",
                            min_size=99999,
                        ),
                    ],
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.halted
        assert "min_size" in result.halt_reason

    async def test_post_check_must_contain_fails(self, tmp_path: Path) -> None:
        """Post-check with must_contain fails when substring is missing."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, ArtifactCheck

        wf = Workflow(
            name="postcheck_contain",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                    post_checks=[
                        ArtifactCheck(
                            path=".factory/reviews/builder-latest.md",
                            must_contain=["EXPECTED_SUBSTRING"],
                        ),
                    ],
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.halted
        assert "must contain" in result.halt_reason

    async def test_post_check_passes(self, tmp_path: Path) -> None:
        """Post-check passes when all conditions are met."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, ArtifactCheck

        wf = Workflow(
            name="postcheck_pass",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                    post_checks=[
                        ArtifactCheck(
                            path=".factory/reviews/builder-latest.md",
                            must_exist=True,
                            must_contain=["ok"],
                        ),
                    ],
                ),
            },
            edges=[],
            start_node="builder",
        )

        agent = FakeAgent(wf)
        executor = WorkflowExecutor(wf, tmp_path, agent_fn=agent, auto_write_outputs=False)
        result = await executor.execute()

        assert result.success, f"Failed: {result.halt_reason}"

    async def test_post_check_skipped_in_dry_run(self, tmp_path: Path) -> None:
        """Post-checks are skipped in dry-run mode."""
        from factory.workflow.executor import WorkflowExecutor
        from factory.workflow.primitives import AgentRole, ArtifactCheck

        wf = Workflow(
            name="postcheck_dryrun",
            nodes={
                "builder": AgentNode(
                    id="builder",
                    role=AgentRole.BUILDER,
                    prompt_template="Build it.",
                    writes={".factory/reviews/builder-latest.md"},
                    post_checks=[
                        ArtifactCheck(path="will-not-exist.txt", must_exist=True),
                    ],
                ),
            },
            edges=[],
            start_node="builder",
        )

        executor = WorkflowExecutor(wf, tmp_path, dry_run=True)
        result = await executor.execute()

        assert result.success, f"Dry-run should skip post_checks: {result.halt_reason}"


# ── Validation checks unit tests ─────────────────────────────────


class TestValidationChecks:
    """Direct tests for the three new validation functions."""

    def test_empty_prompt_detected(self) -> None:
        """AgentNode with empty prompt_template is flagged."""
        from factory.workflow.primitives import AgentRole

        wf = Workflow(
            name="empty_prompt",
            nodes={
                "a": AgentNode(id="a", role=AgentRole.BUILDER),
            },
            edges=[],
            start_node="a",
        )
        issues = validate_workflow(wf)
        assert any("empty prompt_template" in i for i in issues)

    def test_non_empty_prompt_passes(self) -> None:
        """AgentNode with non-empty prompt passes validation."""
        from factory.workflow.primitives import AgentRole

        wf = Workflow(
            name="has_prompt",
            nodes={
                "a": AgentNode(
                    id="a",
                    role=AgentRole.BUILDER,
                    prompt_template="Do the work.",
                ),
            },
            edges=[],
            start_node="a",
        )
        issues = validate_workflow(wf)
        assert not any("empty prompt_template" in i for i in issues)

    def test_gate_with_no_proceed_edge_detected(self) -> None:
        """GateNode with outgoing edges but no PROCEED/RELOOP/unconditional is flagged."""
        from factory.workflow.primitives import Edge, FnNode, GateNode, VerdictType

        wf = Workflow(
            name="no_proceed",
            nodes={
                "fn": FnNode(id="fn", command="echo x"),
                "gate": GateNode(id="gate", evaluator_type="fn"),
                "halt_target": FnNode(id="halt_target", command="echo y"),
            },
            edges=[
                Edge(source="fn", target="gate"),
                Edge(source="gate", target="halt_target", condition=VerdictType.HALT),
            ],
            start_node="fn",
        )
        issues = validate_workflow(wf)
        assert any("PROCEED" in i for i in issues)

    def test_gate_with_proceed_edge_passes(self) -> None:
        """GateNode with PROCEED edge passes validation."""
        from factory.workflow.primitives import Edge, FnNode, GateNode, VerdictType

        wf = Workflow(
            name="has_proceed",
            nodes={
                "fn": FnNode(id="fn", command="echo x"),
                "gate": GateNode(id="gate", evaluator_type="fn"),
                "next": FnNode(id="next", command="echo y"),
            },
            edges=[
                Edge(source="fn", target="gate"),
                Edge(source="gate", target="next", condition=VerdictType.PROCEED),
            ],
            start_node="fn",
        )
        issues = validate_workflow(wf)
        assert not any("PROCEED" in i for i in issues)

    def test_terminal_gate_passes(self) -> None:
        """GateNode with no outgoing edges (terminal) passes validation."""
        from factory.workflow.primitives import Edge, FnNode, GateNode

        wf = Workflow(
            name="terminal_gate",
            nodes={
                "fn": FnNode(id="fn", command="echo x"),
                "gate": GateNode(id="gate", evaluator_type="fn"),
            },
            edges=[
                Edge(source="fn", target="gate"),
            ],
            start_node="fn",
        )
        issues = validate_workflow(wf)
        assert not any("PROCEED" in i for i in issues)
