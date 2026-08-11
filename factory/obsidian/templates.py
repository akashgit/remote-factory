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
