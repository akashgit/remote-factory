"""Tests for factory.obsidian.templates — tag generation and frontmatter constants."""

from factory.obsidian.templates import (
    EXPERIMENT_FRONTMATTER,
    EXPERIMENT_TAG,
    FACTORY_TAG,
    PROJECT_FRONTMATTER,
    PROJECT_TAG,
    STRATEGY_FRONTMATTER,
    STRATEGY_TAG,
    experiment_tags,
    project_tags,
    strategy_tags,
)


class TestConstants:
    """Verify frontmatter tag constants."""

    def test_factory_tag(self):
        assert FACTORY_TAG == "factory"

    def test_experiment_tag(self):
        assert EXPERIMENT_TAG == "experiment"

    def test_project_tag(self):
        assert PROJECT_TAG == "project"

    def test_strategy_tag(self):
        assert STRATEGY_TAG == "strategy"


class TestExperimentFrontmatter:
    """Verify experiment frontmatter has required fields."""

    def test_contains_tags(self):
        assert "tags" in EXPERIMENT_FRONTMATTER

    def test_contains_project(self):
        assert "project" in EXPERIMENT_FRONTMATTER

    def test_contains_experiment_id(self):
        assert "experiment_id" in EXPERIMENT_FRONTMATTER

    def test_contains_verdict(self):
        assert "verdict" in EXPERIMENT_FRONTMATTER

    def test_contains_score_delta(self):
        assert "score_delta" in EXPERIMENT_FRONTMATTER

    def test_contains_date(self):
        assert "date" in EXPERIMENT_FRONTMATTER

    def test_has_six_fields(self):
        assert len(EXPERIMENT_FRONTMATTER) == 6


class TestProjectFrontmatter:
    def test_contains_tags(self):
        assert "tags" in PROJECT_FRONTMATTER

    def test_has_one_field(self):
        assert len(PROJECT_FRONTMATTER) == 1


class TestStrategyFrontmatter:
    def test_contains_tags(self):
        assert "tags" in STRATEGY_FRONTMATTER

    def test_contains_date(self):
        assert "date" in STRATEGY_FRONTMATTER

    def test_has_two_fields(self):
        assert len(STRATEGY_FRONTMATTER) == 2


class TestExperimentTags:
    def test_returns_list(self):
        result = experiment_tags("my-proj")
        assert isinstance(result, list)

    def test_includes_factory_tag(self):
        result = experiment_tags("my-proj")
        assert FACTORY_TAG in result

    def test_includes_experiment_tag(self):
        result = experiment_tags("my-proj")
        assert EXPERIMENT_TAG in result

    def test_includes_project_name(self):
        result = experiment_tags("my-proj")
        assert "my-proj" in result

    def test_has_three_tags(self):
        assert len(experiment_tags("x")) == 3


class TestProjectTags:
    def test_returns_list(self):
        result = project_tags("my-proj")
        assert isinstance(result, list)

    def test_includes_factory_tag(self):
        assert FACTORY_TAG in project_tags("my-proj")

    def test_includes_project_tag(self):
        assert PROJECT_TAG in project_tags("my-proj")

    def test_includes_project_name(self):
        assert "my-proj" in project_tags("my-proj")

    def test_has_three_tags(self):
        assert len(project_tags("x")) == 3


class TestStrategyTags:
    def test_returns_list(self):
        result = strategy_tags("my-proj")
        assert isinstance(result, list)

    def test_includes_factory_tag(self):
        assert FACTORY_TAG in strategy_tags("my-proj")

    def test_includes_strategy_tag(self):
        assert STRATEGY_TAG in strategy_tags("my-proj")

    def test_includes_project_name(self):
        assert "my-proj" in strategy_tags("my-proj")

    def test_has_three_tags(self):
        assert len(strategy_tags("x")) == 3
