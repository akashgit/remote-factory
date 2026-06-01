"""Tests for harness component protocols."""

from factory.harness.adapters import (
    BacklogWorkItemSource,
    CurrentAgentDistributionAdapter,
    CurrentGuardrailAdapter,
    GitHubGitLabIssueSource,
    LocalAgentRuntimeAdapter,
    LocalFactoryStateAdapter,
)
from factory.harness.contracts import (
    DistributionEmitter,
    GuardrailProvider,
    StateBackend,
    WorkItemSource,
    WorkerRuntime,
)


def test_current_adapters_satisfy_protocols():
    assert isinstance(GitHubGitLabIssueSource(), WorkItemSource)
    assert isinstance(BacklogWorkItemSource(), WorkItemSource)
    assert isinstance(LocalAgentRuntimeAdapter(), WorkerRuntime)
    assert isinstance(LocalFactoryStateAdapter(), StateBackend)
    assert isinstance(CurrentGuardrailAdapter(), GuardrailProvider)
    assert isinstance(CurrentAgentDistributionAdapter("claude"), DistributionEmitter)
