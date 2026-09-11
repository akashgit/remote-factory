"""Tests for task-aware create mode directive injection."""

from __future__ import annotations

from pathlib import Path

from factory.cli._task_builder import _build_ceo_task, _build_task_aware_directive


class TestTaskAwareDirective:
    def test_directive_injected_with_toml(self, tmp_path: Path):
        toml = tmp_path / "test.toml"
        toml.write_text(
            '[task]\nname = "test-task"\n'
            'description = "A test task"\n'
            '[scoring]\nmethod = "json"\nmetric_path = "accuracy"\n'
            '[verify]\ncommand = "python eval.py"\n'
            '[constraints]\ntimeout = 300\n'
        )
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "## Create Mode (Task-Aware)" in directive
        assert "test-task" in directive
        assert "json" in directive
        assert "accuracy" in directive
        assert "300s" in directive
        assert "OptKnob" in directive

    def test_directive_includes_optknob_table(self, tmp_path: Path):
        toml = tmp_path / "scored.toml"
        toml.write_text(
            '[task]\nname = "scored"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "pytest -xvs"\n'
        )
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "model" in directive
        assert "threshold" in directive
        assert "prompt" in directive
        assert "topology" in directive.lower()  # "Never auto-generate kind='topology'"

    def test_directive_handles_resolution_failure(self, tmp_path: Path):
        directive = _build_task_aware_directive("nonexistent.toml", tmp_path)
        assert "RESOLUTION FAILED" in directive
        assert "Proceed without task awareness" in directive

    def test_build_ceo_task_includes_directive(self, tmp_path: Path):
        toml = tmp_path / "task.toml"
        toml.write_text(
            '[task]\nname = "my-task"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "true"\n'
        )
        task = _build_ceo_task(
            tmp_path,
            "create",
            create_description="Build a workflow for my-task",
            task_ref=str(toml),
        )
        assert "## Create Mode (Task-Aware)" in task
        assert "my-task" in task
        assert "## Create Mode (New Factory Mode)" in task

    def test_no_directive_without_task_ref(self, tmp_path: Path):
        task = _build_ceo_task(
            tmp_path,
            "create",
            create_description="Build a workflow",
        )
        assert "## Create Mode (Task-Aware)" not in task
        assert "## Create Mode (New Factory Mode)" in task

    def test_no_directive_without_create_description(self, tmp_path: Path):
        toml = tmp_path / "task.toml"
        toml.write_text(
            '[task]\nname = "my-task"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "true"\n'
        )
        task = _build_ceo_task(
            tmp_path,
            "design",
            task_ref=str(toml),
        )
        assert "## Create Mode (Task-Aware)" not in task


class TestScoringContractThreshold:
    def test_threshold_default_none(self):
        from factory.task import ScoringContract

        s = ScoringContract()
        assert s.threshold is None

    def test_threshold_explicit(self):
        from factory.task import ScoringContract

        s = ScoringContract(method="json", threshold=0.85)
        assert s.threshold == 0.85

    def test_threshold_from_toml(self, tmp_path: Path):
        toml = tmp_path / "thresh.toml"
        toml.write_text(
            '[task]\nname = "thresh"\n'
            '[scoring]\nmethod = "json"\nthreshold = 0.7\n'
            '[verify]\ncommand = "python eval.py"\n'
        )
        from factory.task import TaskDefinition

        defn = TaskDefinition.from_toml(toml)
        assert defn.scoring.threshold == 0.7

    def test_threshold_absent_in_toml(self, tmp_path: Path):
        toml = tmp_path / "no_thresh.toml"
        toml.write_text(
            '[task]\nname = "no-thresh"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "true"\n'
        )
        from factory.task import TaskDefinition

        defn = TaskDefinition.from_toml(toml)
        assert defn.scoring.threshold is None


class TestDomainLevelKnobs:
    def test_directive_with_threshold(self, tmp_path: Path):
        toml = tmp_path / "with_thresh.toml"
        toml.write_text(
            '[task]\nname = "thresh-task"\n'
            '[scoring]\nmethod = "json"\nthreshold = 0.8\n'
            '[verify]\ncommand = "python eval.py"\n'
        )
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "Domain-Level OptKnob" in directive
        assert "0.8" in directive

    def test_directive_without_threshold(self, tmp_path: Path):
        toml = tmp_path / "no_thresh.toml"
        toml.write_text(
            '[task]\nname = "no-thresh"\n'
            '[scoring]\nmethod = "exit_code"\n'
            '[verify]\ncommand = "true"\n'
        )
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "Domain-Level OptKnob" in directive
        assert "No threshold configured" in directive


class TestDataNodeOptKnobs:
    """Tests for DataNode OptKnob derivation rules in the task-aware directive."""

    def _make_toml(self, tmp_path: Path) -> Path:
        toml = tmp_path / "dn-task.toml"
        toml.write_text(
            '[task]\nname = "dn-task"\n'
            '[scoring]\nmethod = "json"\nmetric_path = "score"\n'
            '[verify]\ncommand = "python eval.py"\n'
        )
        return toml

    def test_directive_contains_datanode_section(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "### DataNode OptKnob Auto-Generation" in directive

    def test_parallelism_knob_in_directive(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "DataNode.parallelism" in directive
        assert "Number of concurrent data items processed in parallel" in directive

    def test_limit_knob_in_directive(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "DataNode.limit" in directive
        assert "Maximum number of data items to process per run" in directive
        # limit is conditional — "when set, not None"
        assert "when set" in directive

    def test_max_items_knob_in_directive(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        assert "DataNode.max_items" in directive
        assert "Safety ceiling for total data items loaded" in directive

    def test_no_topology_knobs_for_datanode(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        # split/shuffle should only appear in the prohibition instruction, not as table rows
        assert "Do NOT add topology knobs for DataNode.split or DataNode.shuffle" in directive
        dn_start = directive.index("### DataNode OptKnob Auto-Generation")
        dn_section = directive[dn_start:directive.index("Do NOT add topology", dn_start)]
        table_rows = [ln for ln in dn_section.splitlines() if ln.startswith("| DataNode.")]
        for row in table_rows:
            assert "split" not in row
            assert "shuffle" not in row

    def test_datanode_knobs_are_threshold_kind(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        # Extract the DataNode table section
        dn_start = directive.index("### DataNode OptKnob Auto-Generation")
        dn_section = directive[dn_start:directive.index("Never auto-generate", dn_start)]
        # All three DataNode table rows should specify 'threshold' kind
        lines = [ln for ln in dn_section.splitlines() if ln.startswith("| DataNode.")]
        assert len(lines) == 3
        for line in lines:
            assert "threshold" in line

    def test_parallelism_not_expandable(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        dn_start = directive.index("### DataNode OptKnob Auto-Generation")
        dn_section = directive[dn_start:directive.index("Never auto-generate", dn_start)]
        parallelism_line = [
            ln for ln in dn_section.splitlines() if "DataNode.parallelism" in ln
        ][0]
        assert "| False |" in parallelism_line

    def test_limit_and_max_items_expandable(self, tmp_path: Path):
        toml = self._make_toml(tmp_path)
        directive = _build_task_aware_directive(str(toml), tmp_path)
        dn_start = directive.index("### DataNode OptKnob Auto-Generation")
        dn_section = directive[dn_start:directive.index("Never auto-generate", dn_start)]
        for field in ("DataNode.limit", "DataNode.max_items"):
            field_line = [ln for ln in dn_section.splitlines() if field in ln][0]
            assert "| True |" in field_line


class TestTaskSetupWorkflow:
    def test_task_setup_workflow_validates(self):
        from factory.workflow.definitions import task_setup_workflow

        wf = task_setup_workflow()
        assert wf.name == "task-setup"
        issues = wf.validate_graph()
        assert issues == [], f"Validation issues: {issues}"

    def test_task_setup_registered(self):
        from factory.workflow.definitions import _get_builtin_registry

        reg = _get_builtin_registry()
        assert "task-setup" in reg

    def test_task_setup_trigger(self):
        from factory.models import ProjectState
        from factory.workflow.definitions import task_setup_workflow

        wf = task_setup_workflow()
        assert wf.trigger is not None
        assert wf.trigger(ProjectState.NO_REPO, {"mode": "task-setup"})
        assert not wf.trigger(ProjectState.NO_REPO, {"mode": "create"})

    def test_task_setup_has_expected_nodes(self):
        from factory.workflow.definitions import task_setup_workflow

        wf = task_setup_workflow()
        node_ids = set(wf.nodes.keys())
        assert "fork_research" in node_ids
        assert "researcher_domain" in node_ids
        assert "researcher_verification" in node_ids
        assert "join_research" in node_ids
        assert "gate_research" in node_ids
        assert "strategist" in node_ids
        assert "gate_strategy" in node_ids
        assert "builder" in node_ids
        assert "validate_task" in node_ids
        assert "archivist" in node_ids

    def test_task_setup_in_ceo_modes(self):
        from factory.cli._helpers import CEO_MODES

        assert "task-setup" in CEO_MODES

    def test_task_setup_skill_meta(self):
        from factory.workflow.skill_export import WORKFLOW_META

        assert "task-setup" in WORKFLOW_META
        meta = WORKFLOW_META["task-setup"]
        assert "description" in meta
        assert "scaffold" in meta["description"].lower() or "task" in meta["description"].lower()
