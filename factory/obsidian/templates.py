"""Obsidian note templates — frontmatter schemas for factory notes."""

from __future__ import annotations

# Frontmatter tag constants
FACTORY_TAG = "factory"
EXPERIMENT_TAG = "experiment"
PROJECT_TAG = "project"
STRATEGY_TAG = "strategy"

# Required frontmatter fields per note type
EXPERIMENT_FRONTMATTER = [
    "tags",
    "project",
    "experiment_id",
    "verdict",
    "score_delta",
    "date",
]

PROJECT_FRONTMATTER = [
    "tags",
]

STRATEGY_FRONTMATTER = [
    "tags",
    "date",
]


def experiment_tags(project_name: str) -> list[str]:
    """Return standard tags for an experiment note."""
    return [FACTORY_TAG, EXPERIMENT_TAG, project_name]


def project_tags(project_name: str) -> list[str]:
    """Return standard tags for a project dashboard note."""
    return [FACTORY_TAG, PROJECT_TAG, project_name]


def strategy_tags(project_name: str) -> list[str]:
    """Return standard tags for a strategy note."""
    return [FACTORY_TAG, STRATEGY_TAG, project_name]
