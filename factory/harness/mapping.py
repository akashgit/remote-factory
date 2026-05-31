"""Current implementation mapping for the Phase 0 harness abstraction."""

from __future__ import annotations


CURRENT_COMPONENT_MAPPING: dict[str, list[str]] = {
    "project_context": [
        "factory.cli",
        "factory.registry",
        "factory.store",
    ],
    "work_item": [
        "factory.issue",
        "factory.study",
        "factory.cli --focus handling",
    ],
    "execution_contract": [
        "factory.models.FactoryConfig",
        "factory.models.ResearchTarget",
        "factory.cli CEO task construction",
    ],
    "worker_runtime": [
        "factory.runners",
        "factory.agents.runner",
    ],
    "state_backend": [
        "factory.store",
        "factory.events",
        "factory.registry",
        "factory.report",
    ],
    "guardrail": [
        "factory.eval",
        "factory.precheck",
        "factory.clean_pr",
        "factory.research.leakage",
    ],
    "distribution": [
        "factory.agents.plugin",
        "factory.cli.cmd_install",
        "scripts.sync_agents",
    ],
}


def current_component_mapping() -> dict[str, list[str]]:
    """Return a copy of the current module-to-contract mapping."""
    return {name: modules.copy() for name, modules in CURRENT_COMPONENT_MAPPING.items()}
