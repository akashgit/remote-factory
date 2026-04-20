"""Obsidian note templates — frontmatter schemas for factory notes."""

from __future__ import annotations

# Frontmatter tag constants
FACTORY_TAG = "factory"
EXPERIMENT_TAG = "experiment"
PROJECT_TAG = "project"
STRATEGY_TAG = "strategy"
DECISION_TAG = "decision"
CONCEPT_TAG = "concept"
SOURCE_TAG = "source"

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

DECISION_FRONTMATTER = [
    "tags",
    "project",
    "date",
    "context",
    "outcome",
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


def decision_tags(project_name: str) -> list[str]:
    """Return standard tags for a decision note."""
    return [FACTORY_TAG, DECISION_TAG, project_name]


def experiment_note_path(project_name: str, experiment_id: int) -> str:
    """Return the canonical vault path for an experiment note.

    Experiment notes live in ``10-Projects/<project>/Experiments/`` so that
    the eval ``doc_ratio`` sub-score finds them reliably.
    """
    return f"10-Projects/{project_name}/Experiments/{project_name}-{experiment_id:03d}"


def wikilink(title: str) -> str:
    """Return an Obsidian wikilink."""
    return f"[[{title}]]"
