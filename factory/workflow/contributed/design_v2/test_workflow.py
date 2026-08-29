"""In-package smoke tests for the design-v2 contributed workflow."""

from __future__ import annotations

from factory.workflow.contributed.design_v2 import meta, workflow
from factory.workflow.definitions import register_all


class TestDesignV2Smoke:
    def test_meta_name(self) -> None:
        assert meta["name"] == "design-v2"

    def test_meta_description(self) -> None:
        assert len(meta["description"]) > 0

    def test_graph_validates(self) -> None:
        wf = workflow()
        issues = wf.validate_graph()
        assert issues == [], f"design-v2 has validation issues: {issues}"

    def test_registered_in_register_all(self) -> None:
        workflows = register_all()
        assert "design-v2" in workflows

    def test_registered_workflow_valid(self) -> None:
        workflows = register_all()
        wf = workflows["design-v2"]
        issues = wf.validate_graph()
        assert issues == [], f"Registered design-v2 has issues: {issues}"
