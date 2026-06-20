# re:factory Meta-Harness Specification

Status: Draft v1 (language-agnostic)

Purpose: Define a meta-harness that orchestrates coding agents through bounded,
measurable, reversible SDLC cycles.

## Normative Language

The key words `MUST`, `MUST NOT`, `REQUIRED`, `SHOULD`, `SHOULD NOT`,
`RECOMMENDED`, `MAY`, and `OPTIONAL` in this document are to be interpreted as
described in RFC 2119.

`Implementation-defined` means the behavior is part of the implementation
contract, but this specification does not prescribe one universal policy.
Implementations MUST document the selected behavior.

## 1. Problem Statement

re:factory is a meta-harness for agentic software evolution. It accepts software
work, binds that work to a project context, dispatches coding agents under an
execution contract, validates the result through guardrails, records evidence,
and converts the outcome into an explicit decision and durable memory.

The system solves five operational problems:

- It turns agentic coding into a repeatable SDLC lifecycle instead of ad hoc
  prompts or scripts.
- It separates project scope from repository checkouts, runtime execution, and
  product packaging.
- It makes each change measurable and reversible through evidence, guardrails,
  and explicit decisions.
- It keeps project state durable enough to support resume, review, and
  learning.
- It is designed so future implementations can preserve the same lifecycle
  semantics without changing the meaning of a project cycle.

Important boundary:

- re:factory is a meta-harness, not a general-purpose workflow engine.
- A deployment profile is a bundle of component implementations, not a separate
  implementation of the domain model.
- Agent execution MAY end at a handoff state; a successful run does not
  necessarily mean code was merged or released.
- Trust, approval, sandboxing, and external write policies are
  implementation-defined and MUST be documented by the implementation.

## 2. Goals and Non-Goals

### 2.1 Goals

- Represent software work as normalized work items.
- Bind work items to a durable project context.
- Support projects that bind the repository or execution context needed for
  work.
- Dispatch agents through explicit execution contracts.
- Preserve evidence for diffs, logs, evals, reviews, reports, and artifacts.
- Validate outcomes through guardrails before a decision is accepted.
- Record decisions as first-class lifecycle outputs.
- Maintain durable memory for project learning and future planning.
- Preserve durable state for resume, review, and learning, with optional
  reconciliation to external systems where supported.
- Treat the CLI-local profile as the primary compatibility surface.
- Allow future deployment profiles to bundle different runtimes, state
  backends, guardrails, and output surfaces while preserving common lifecycle
  semantics.

### 2.2 Non-Goals

- Prescribing a specific source-code layout or module structure.
- Requiring a managed service or hosted control plane.
- Requiring Jira, Linear, GitHub, GitLab, or any specific tracker.
- Requiring a rich web UI or dashboard.
- Mandating one sandbox, approval, or operator-confirmation policy.
- Mandating that agents perform ticket writes, PR creation, or merge actions.
- Requiring multi-repository orchestration or multi-user shared-state
  collaboration as part of core conformance.
- Replacing human review, CI policy, or repository governance.

## 3. System Overview

### 3.1 Main Components

1. `Deployment Profile`
   - Names a product surface and selected component implementations.
   - Declares runtime, state handling, guardrails, output surfaces, and policy
     sources.
   - Does not redefine the core domain model.
2. `Project Resolver`
   - Converts user input or configuration into a project context.
   - Binds the repository, checkout, and state locations required by the
     selected implementation.
3. `Work Item Source`
   - Reads work from prompts, backlog entries, issues, tickets, or research
     targets.
   - Normalizes external payloads into stable work-item records.
4. `Contract Builder`
   - Converts project policy and work-item scope into an execution contract.
   - Identifies mutable surfaces, fixed surfaces, required checks, budgets, and
     expected evidence.
5. `Lifecycle Coordinator`
   - Owns the lifecycle transition from intake through learning.
   - Decides when to dispatch, validate, retry, park, or escalate work.
   - Converts worker and guardrail outcomes into decision records.
6. `Worker Runtime`
   - Runs a coding agent or worker against an execution contract.
   - Returns output, status, logs, and implementation-defined telemetry.
7. `Guardrail Provider`
   - Evaluates tests, lint, type checks, eval metrics, CI state, review policy,
     scope rules, leakage rules, security policy, or other checks.
8. `State Backend`
   - Persists project records, evidence references, decisions, and memory.
   - MAY mirror or reconcile state with external systems when supported.
9. `Memory System`
   - Preserves durable learnings, observations, playbook evidence, reports, and
     handoff records.
10. `Output Surface`
    - Publishes or materializes implementation-defined lifecycle outputs such as
      reviews, reports, generated assets, or external updates.

### 3.2 Abstraction Levels

re:factory is easiest to port when kept in these layers:

1. `Policy Layer`
   - Project goal, scope, constraints, prompts, and validation policy.
2. `Profile Layer`
   - User-facing surfaces and component bundles.
3. `Coordination Layer`
   - Lifecycle transitions, dispatch, validation ordering, decisions, retry, and
     resume.
4. `Execution Layer`
   - Worker runtime, repository checkout/worktree behavior, and agent protocol.
5. `State Layer`
   - Project records, event streams, materialized views, and external bindings
     when present.
6. `Guardrail and Evidence Layer`
   - Checks, artifacts, logs, scores, reviews, and reports.
7. `Memory and Observability Layer`
   - Human/operator-visible status, archives, summaries, and learned rules.

### 3.3 External Dependencies

Implementations MAY depend on:

- Local filesystem state.
- Git repositories and worktrees.
- Coding-agent executables or managed agent services.
- Issue trackers, ticket systems, or PR systems.
- CI, review, or security-scanning systems.
- Host authentication for agent runtimes and external state backends.

## 4. Core Domain Model

### 4.1 Project

A `Project` is the durable SDLC boundary for work, evidence, decisions, and
memory.

Logical fields:

- `project_id`: stable project identifier.
- `name`: human-readable project name.
- `goal`: project objective or mission statement.
- `repo_bindings`: repository or checkout bindings associated with the project.
- `state_bindings`: durable or external state substrates associated with the
  project.
- `policy_refs`: references to project policy/configuration.
- `memory_refs`: references to durable project memory.

Rules:

- A project MUST bind the execution context needed for the work.
- Implementations MAY realize that execution context as one repository binding
  or multiple repository bindings.
- A single local repository binding with local durable state is sufficient for
  core conformance.
- Work items, decisions, and memory belong to the project.
- Diffs, branches, and checkouts belong to repository bindings.
- Runtime and deployment profile are not project-owned.

### 4.2 Repo Binding

A `RepoBinding` identifies a repository or worktree participating in a project.

Logical fields:

- `repo_id`: stable identifier within the project.
- `path`: local path, if available.
- `remote`: remote repository identifier or URL, if available.
- `role`: implementation-defined role such as `primary`.
- `default_branch`: default integration branch, if known.
- `checkout`: checkout or worktree metadata, if applicable.

### 4.3 State Binding

A `StateBinding` identifies a state substrate associated with a project.

Examples:

- local project state
- GitHub issue or PR state
- GitLab issue or merge-request state
- Jira ticket state
- Linear issue state
- managed service state

State bindings MUST NOT imply that runtime execution happens in that state
system.

A single local durable state substrate is sufficient for core conformance.

### 4.4 Work Item

A `WorkItem` is a unit of work entering the lifecycle.

Sources MAY include:

- direct CLI prompt
- focus request
- backlog item
- issue
- ticket
- research target

Logical fields:

- `work_item_id`
- `kind`
- `title`
- `body`
- `labels`
- `repo_ids` (OPTIONAL)
- `external_refs`
- `metadata`

Implementations SHOULD preserve both the normalized work item and enough source
metadata to trace it back to its origin.

### 4.5 Execution Contract

An `ExecutionContract` defines the scope and policy for one execution attempt or
cycle.

Logical fields:

- `contract_id`
- `project_id`
- `work_item_id`
- `scope`
- `mutable_surfaces`
- `fixed_surfaces`
- `required_checks`
- `budget`
- `expected_evidence`
- `report_schema` (OPTIONAL)

Worker runtimes MUST receive enough contract information to respect scope,
surface, and reporting requirements. This information MAY be conveyed through
structured payloads, prompt content, or other implementation-defined
mechanisms.

### 4.6 Worker Runtime

A `WorkerRuntime` executes agent work under an execution contract.

Examples:

- local subprocess agent
- interactive terminal or tmux-backed agent
- background session agent (fire-and-poll, observable via agent management interface)
- plugin asset worker
- managed remote agent

Runtime selection is implementation-defined. Runtime behavior MUST NOT change the
meaning of project, work-item, evidence, or decision records.

### 4.7 Guardrail

A `Guardrail` is a validation or policy check whose result contributes to a
decision.

Examples:

- tests
- lint
- type checks
- eval metrics
- CI status
- code review
- security review
- scope or immutability checks
- leakage checks

Guardrail outcomes SHOULD be recorded as evidence.

### 4.8 Evidence

`Evidence` is immutable or append-only support for a lifecycle decision.

Examples:

- diffs
- logs
- eval results
- review findings
- CI status
- generated reports
- artifacts

Evidence SHOULD include project identity and MAY include repository identity,
work-item identity, runtime identity, and external references.

### 4.9 Decision

A `Decision` is the lifecycle outcome accepted from evidence and guardrail
results.

Common decision kinds include:

- `keep`
- `revert`
- `park`
- `retry`
- `escalate`
- `error`

Implementations MAY expose additional publication or escalation outcomes.

Decisions MUST include rationale and SHOULD reference supporting evidence.

### 4.10 Memory

`Memory` is durable knowledge used by future cycles.

Examples:

- experiment archives
- observations
- playbook rules
- reinforced or contradicted lessons
- handoff snapshots
- performance reports

Memory records SHOULD distinguish durable learnings from reconstructable runtime
state.

### 4.11 Deployment Profile

A `DeploymentProfile` is a named assembly of component implementations.

Logical fields:

- `name`
- `surface`
- `runtime`
- `state_backend`
- `guardrails`
- `output_surfaces`
- `policy_sources`

The `cli-local` deployment profile is the primary product surface for this
specification. Other profiles MAY expose different surfaces, but SHOULD
preserve the lifecycle semantics of this specification.

### 4.12 Shared State Records (OPTIONAL)

Implementations that support shared, externally reconciled, or multi-actor
state MAY represent project state as `StateRecord`s.

Logical fields:

- `id`
- `kind`
- `project_id`
- `repo_id` (OPTIONAL)
- `source`
- `actor`
- `revision`
- `parent_ids`
- `created_at`
- `updated_at`
- `payload`

A `StateConflict` records an unresolved merge problem when such
implementations detect one.

Implementations that do not expose shared-state semantics do not need to model
state records or conflicts as first-class domain objects.

## 5. Lifecycle Specification

The lifecycle is:

```text
Intake → Scope → Dispatch → Execute → Validate → Decide → Publish → Learn → Resume
```

### 5.1 Intake

The system accepts work from one or more work-item sources and normalizes it into
a work item.

### 5.2 Scope

The system binds the work item to a project context and derives an execution
contract.

### 5.3 Dispatch

The system selects a worker runtime and starts an execution attempt.
Dispatch MUST preserve enough state to support observability and recovery.

Dispatch modes include:

- **synchronous** — the caller blocks until the worker completes (default)
- **interactive** — the worker runs in a user-facing terminal session
- **background** — the worker is launched as a detached session; the caller
  polls for completion and collects output when the session finishes

Dispatch mode MAY be scoped independently per lifecycle tier. For example, a
coordinator MAY run synchronously while its delegated workers dispatch in
background mode, allowing the operator to observe the coordinator while
workers remain visible through an agent management interface.

Dispatch mode is a runtime concern. It MUST NOT change the semantics of the
execution contract, evidence records, or decision lifecycle.

### 5.4 Execute

The worker runtime performs the scoped work. It SHOULD emit logs, status, and
artifacts sufficient for validation and review.

### 5.5 Validate

Guardrails evaluate the produced state, artifacts, or external checks.
Validation failures MUST be visible to the decision step.

### 5.6 Decide

The lifecycle coordinator records an explicit decision. Decisions SHOULD be
derived from evidence and guardrail outcomes.

### 5.7 Publish

If an implementation supports publishing, it MAY update external systems such as
branches, PRs, comments, ticket state, or managed-state records. Publishing
behavior is implementation-defined.

### 5.8 Learn

The memory system records durable learnings, observations, and reports. Memory
SHOULD be usable by future work-item selection, scoping, and validation.

### 5.9 Resume

The system SHOULD be able to reconstruct useful lifecycle state from durable
records, evidence, external bindings, and materialized views. Exact in-memory
runtime state is implementation-defined.

## 6. Deployment Profile Specification

Deployment profiles bundle component implementations.

### 6.1 `cli-local` Profile

The `cli-local` profile is the primary compatibility surface.

It consists of:

- CLI command surface
- local worker runtime (supports multiple dispatch modes as implementation-defined options)
- local project state backend
- local guardrail providers
- implementation-defined local output surfaces

### 6.2 Extension Profiles

Other deployment profiles MAY exist. This specification does not require any
fixed catalog beyond `cli-local`.

Extension profiles MUST document selected component implementations and
SHOULD preserve the lifecycle semantics in this specification.

## 7. Shared-State Semantics (OPTIONAL)

This section applies only to implementations that support shared, externally
reconciled, or multi-actor state.

State backends SHOULD prefer append-only events and immutable evidence over
destructive updates.

Materialized views SHOULD be rebuildable from durable records.

Record kinds MAY define different merge policies.

When an implementation supports multi-user state, it MUST represent unresolved
important conflicts explicitly rather than silently applying last-writer-wins.

## 8. Guardrails and Trust Policy

Each implementation MUST document its trust and safety posture.

If an implementation defines additional deployment profiles, each profile MUST
document any trust or policy differences that affect execution.

Implementation-defined policy areas include:

- sandboxing
- approval prompts
- network access
- external writes
- merge authority
- credential handling
- destructive filesystem operations

Guardrails SHOULD be explicit, observable, and traceable to evidence.

## 9. Conformance

### 9.1 Core Conformance

A conforming implementation MUST:

- represent work as work items
- bind work to project context
- distinguish project-level lifecycle state from checkout or runtime state
- execute work under an execution contract
- record evidence for validation and decisions
- run or consume guardrail outcomes before accepting decisions
- record explicit decisions
- preserve durable memory or reports
- document selected deployment profile components
- document implementation-defined trust and safety policy

### 9.2 Extension Conformance

An implementation that supports multi-repo projects SHOULD:

- identify repository bindings by stable IDs
- attach repo-specific evidence to the relevant binding
- keep project-level decisions and memory separate from checkout state

An implementation that supports external state SHOULD:

- preserve source identifiers and URLs
- normalize external payloads into work items or state records
- define reconciliation behavior for source state changes

An implementation that supports multi-user state MUST:

- track actor and source metadata for important records
- define merge policy per record kind
- produce explicit conflict records for unresolved important conflicts

An implementation that supports additional deployment profiles SHOULD:

- describe the component bundle
- preserve the domain model
- document deviations from CLI-local behavior
