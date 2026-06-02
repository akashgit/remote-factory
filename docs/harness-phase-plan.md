# Harness Abstraction Phase Plan

This document contains the non-normative implementation plan for the
meta-harness abstraction described in `SPEC.md`.

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

## Phase 0: Additive Wrappers

Introduce explicit models, protocols, bundle descriptors, and wrappers over
current behavior.

Constraints:

- Do not change CLI behavior.
- Do not change existing call sites unless required by tests/imports.
- Do not migrate `.factory` schemas.
- Do not change generated Claude Code or Codex agent output.

Deliverables:

- `factory/harness/models.py`
- `factory/harness/contracts.py`
- `factory/harness/adapters.py`
- `factory/harness/distribution.py`
- tests proving wrappers describe current behavior

## Phase 1: Internal Delegation

Gradually route selected internals through the new contracts while preserving
the public CLI contract.

Candidate seams:

- `factory install` through distribution emitters
- issue/focus normalization through work-item sources
- agent invocation through worker runtime adapters
- read-only state queries through state backend adapters

## Phase 2: External State and Multi-Repo

Add real multi-repo project bindings and external state sources through the
state and work-item contracts.

Candidate extensions:

- GitHub/GitLab issue and PR state backends
- Jira and Linear work-item sources
- project configuration that binds multiple repositories
- state conflict records and merge policy tests

## Phase 3: Managed Agents

Add managed runtime and managed state implementations while preserving CLI
visibility and control.

Candidate extensions:

- managed worker runtime adapter
- managed state backend adapter
- distribution descriptors for hosted operation
- CLI-visible status and audit surfaces
