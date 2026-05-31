# re:factory Meta-Harness Specification

This document defines the long-term abstraction for re:factory as a
component-based SDLC meta-harness. It is intentionally written as an internal
design guide: current behavior remains the compatibility baseline, especially
the `factory` CLI.

## North Star

re:factory turns software work into bounded, measurable, reversible SDLC
cycles:

```text
Intake → Scope → Dispatch → Execute → Validate → Decide → Publish → Learn → Resume
```

The current implementation already does this through the CLI, local agent
subprocesses, `.factory` state, evals, reviews, and archives. The abstraction
below makes those concepts explicit so the system can grow into native plugins,
external state systems, multi-repo projects, multi-user operation, and managed
agents without redesigning the product surface.

## Distribution Model

A distribution is a named bundle of component implementations plus packaging
conventions. It is not a separate implementation of the harness core.

The current primary distribution is `cli-local`:

- surface: `factory` CLI commands
- runtime: local agent subprocesses via runner backends
- state: `.factory`, event logs, registry, reports, archive
- guardrails: local evals, precheck, hard constraints, leakage checks, clean PR
- emitters: Claude Code and Codex agent files installed by the CLI

Future distributions should preserve CLI-compatible semantics where possible:

- `plugin-native`: Claude Code/Codex/plugin assets generated from native specs
- `hybrid`: local execution plus external issue/PR/ticket state bindings
- `managed`: hosted runtime/state while preserving CLI visibility and control

## Component Contracts

The harness is described by component contracts. Each distribution selects
implementations for these contracts.

### Project Context

`ProjectContext` is the durable SDLC boundary. A project may bind one repository
today and multiple repositories later.

Project-owned concepts:

- `ProjectContext`: project identity, name, goal, repo bindings, state bindings
- `RepoBinding`: repo/worktree path, remote, role, branch, checkout metadata
- `StateBinding`: local or external state locations bound to the project
- `WorkItem`: prompt, focus request, backlog item, issue, ticket, research target
- `Memory`: archive, observations, reports, playbook evidence, handoffs

### Lifecycle Data

Lifecycle concepts are produced and consumed during a cycle:

- `ExecutionContract`: scope, mutable/fixed surfaces, budget, checks, report schema
- `Evidence`: diffs, logs, eval scores, CI state, issue/PR reports, artifacts
- `Decision`: keep, revert, merge, park, retry, escalate

### Platform Components

These are not project-owned; distributions choose implementations:

- `WorkerRuntime`: local subprocess agents, plugin assets, or managed agents
- `StateBackend`: `.factory`, GitHub/GitLab, Jira/Linear, or managed state
- `Guardrail`: tests, lint, typecheck, evals, CI, review, security, leakage checks
- `DistributionEmitter`: Claude/Codex files, plugin packages, managed manifests

## Project, Repo, and State Separation

Project identity, repo identity, state storage, runtime execution, and
distribution packaging are separate axes.

Rules:

- Existing CLI path input maps to an implicit single-repo `ProjectContext`.
- Future configuration may bind multiple repos under one project state graph.
- Experiments, work items, decisions, and memory belong to the project.
- Diffs, branches, and checkouts belong to repo bindings.
- Runtime and distribution are never project-owned.
- `.factory` remains the default local state implementation.

## Multi-User State Principles

State must eventually support multiple users and multiple state stores.

Design principles:

- Prefer append-only events and immutable evidence over overwrites.
- Materialized views should be rebuildable from events and snapshots.
- Records should carry `id`, `kind`, `project_id`, optional `repo_id`, `source`,
  `actor`, `revision`, timestamps, and causal parent IDs.
- Important state must not silently use last-writer-wins.

Merge policies should be explicit per record kind:

- evidence/artifacts: append-only
- work-item status: source-aware reconciliation
- decisions/verdicts: single-owner or conflict-blocking
- memory/playbook rules: evidence ledger with reinforce/contradict counts
- config/contracts: optimistic concurrency with explicit conflict records

Conflicts are represented as `StateConflict` records.

## Current Module Mapping

| Contract | Current implementation |
|---|---|
| Project context | CLI path/project resolution, registry, `.factory` config |
| Work item | `factory.issue`, backlog helpers in `factory.study`, CLI `--focus` |
| Execution contract | `FactoryConfig`, research configs, CLI CEO task construction |
| Worker runtime | `factory.runners`, `factory.agents.runner` |
| State backend | `factory.store`, `factory.events`, `factory.registry`, `factory.report` |
| Guardrails | `factory.eval`, `factory.precheck`, `factory.clean_pr`, leakage checks |
| Evidence | experiments directories, eval JSON, diffs, review files, reports |
| Decision | experiment verdicts, precheck results, CEO review verdicts |
| Memory | archive, reports, ACE playbooks, checkpoint/resume, handoffs |
| Distribution emitters | `factory.agents.plugin`, `factory install`, `scripts/sync_agents.py` |

## Phase Roadmap

### Phase 0: Additive Wrappers

Introduce explicit models, protocols, bundle descriptors, and wrappers over
current behavior. Do not change CLI behavior, call sites, `.factory` schemas, or
generated agent output.

### Phase 1: Internal Delegation

Gradually route selected internals through the new contracts while preserving
the public CLI contract. Candidate seams: install emitters, work-item
normalization, runtime invocation, and state reads.

### Phase 2: External State and Multi-Repo

Add real multi-repo project bindings and external state sources such as
GitHub/GitLab/Jira/Linear through the state and work-item contracts.

### Phase 3: Managed Agents

Add managed runtime/state implementations while keeping the CLI as the primary
control and observability surface.
