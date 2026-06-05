# Build-Root: Factory Mode for Verified Build Environment Construction

## Vision

Build-root is a new Factory mode — peer to build, improve, research, and discover — that takes a project repo and version tag as input and produces a verified build root as output: a Containerfile, local dependency repository, and build script that together produce a green build. It uses a 4-stage gated pipeline where each stage must reach its terminal condition before the next stage begins, and each stage runs its own internal auto-research loop (diagnose → fix → re-eval) to get there.

## Problem Statement

**Input:** A project repository + a version tag (e.g., `spring-framework @ v5.2.9`).

**Output:** A verified build root — a complete, reproducible environment where the old version compiles from source. Concretely:
- A `Containerfile` specifying the exact OS, JDK, and toolchain
- A `local-repo/` directory containing recovered/rebuilt dependencies not available from public repositories
- A `build.sh` script (with Gradle init scripts, task exclusions, env config) that produces a green build
- Evidence of a green build (logs, JARs, test results)

**What "build root" means:** The minimal, self-contained environment definition from which a historical version can be built from unmodified source. The build root is infrastructure, not source — it never modifies the project's own code, build.gradle, settings.gradle, or gradle-wrapper.properties. Instead it wraps the source in the correct container, injects repository overrides via init scripts, and supplies missing artifacts via a local repository.

## The 4-Stage Gated Pipeline

The pipeline is strictly sequential. Each stage has a terminal condition that must be met before the next stage begins. If a stage cannot reach its terminal condition, the pipeline halts and raises an expert gate. Within each stage, an auto-research loop iterates: diagnose the current failure → look up known fixes / dead-ends → apply fix or attempt recovery → re-evaluate → repeat until terminal condition is met or progress plateaus.

### Stage 1: DEP RESOLVE — "Do all the pieces exist?"

- **What:** Resolve every dependency declared by the project. Run `./gradlew dependencies --configuration compileClasspath --continue` across all modules inside the container. Parse the output to classify every dependency as `resolved` (available from configured repos), `missing` (cannot be found), or `conflict` (version conflict). This includes build plugins, buildscript classpath dependencies, and runtime compile dependencies.
- **How:** The auto-research loop within this stage: (1) Run dependency resolution, parse for FAILED markers and 401/403 errors. (2) Consult the known-fixes database for matching patterns (e.g., `repo.spring.io` → inject `mavenCentral()` via init script). (3) Check dead-end records for artifacts confirmed irrecoverable. (4) Apply fixes to mutable surfaces (init scripts, Containerfile). (5) Re-run resolution and check if the count of unresolved dependencies decreased. (6) Repeat until no unresolved dependencies remain OR all remaining unresolved deps are flagged for Stage 2 recovery. Research shows that a single repo-auth fix (injecting `mavenCentral()` into all three resolution paths: `allprojects.repositories`, `allprojects.buildscript.repositories`, `settingsEvaluated.pluginManagement.repositories`) unblocks the majority of dependencies in one shot, since Spring's 401 errors from `repo.spring.io` cascade to every module.
- **Why:** Dependency resolution is the foundation — if deps don't resolve, compilation can't run. Research found that Spring v5.1.0 uses ONLY `repo.spring.io/libs-release` with no `mavenCentral()` fallback, meaning every single dependency fails. By isolating this as Stage 1, the agent focuses entirely on making all pieces available before attempting to assemble them.
- **Terminal condition:** Every dependency resolves successfully — zero FAILED markers in dependency resolution output. Any dependency that cannot be resolved is either (a) recovered in Stage 2 or (b) excluded with documented justification and a dead-end record.

### Stage 2: ARTIFACT RECOVERY — "Can we get what's missing?"

- **What:** For each dependency that Stage 1 could not resolve and that has no known fix or dead-end record, attempt to find the artifact's source code, rebuild it from source, verify API compatibility, and install it to the local repository. This is the hardest stage — research calls it "where the most auto-research time goes."
- **How:** The auto-research loop within this stage processes each missing artifact: (1) **Search**: Check Maven Central mirrors, archive.org Wayback Machine, GitHub/GitLab repos matching the groupId, source JARs on Maven Central, and the artifact's POM `scm` URL. (2) **Build**: If source is found, build it using its own build system (Maven/Gradle), targeting the exact version tag. (3) **Verify**: Compare the rebuilt JAR's public API (class names, method signatures) against what the consuming project expects. (4) **Install**: Publish to `local-repo/` and add the repo to the container's init script. (5) **Record**: If recovery fails after exhausting strategies, record a dead-end with strategies tried, and apply a workaround (typically module exclusion via `-x` flag or dependency substitution). Recursive recovery is capped at depth 3. Research identified specific artifacts that will hit this stage: `propdeps-plugin:0.0.9.RELEASE` (substitute: `cn.bestwu.gradle:propdeps-plugin:0.0.10`), `docbook-reference-plugin:0.3.1` (no source, must exclude docs tasks), `com.ibm.websphere:uow:6.0.2.17` (proprietary IBM artifact, must exclude WebSphere support).
- **Why:** Some dependencies are genuinely gone from the internet. Chainguard's "Java Archaeology at Massive Scale" research confirms that source code hosting disappears (Google Code, java.net), metadata is "spotty, faulty, or very difficult to locate," and some artifacts were never published outside private repos. A systematic recovery engine with a dead-end registry prevents unbounded searching.
- **Terminal condition:** No unresolved dependencies remain. Every artifact from Stage 1's unresolved list is either recovered (installed to local-repo), substituted (alternative artifact mapped in known-fixes), or excluded (dead-end recorded with justification). Re-running Stage 1's dependency resolution produces zero FAILED markers.

### Stage 3: COMPILE — "Does it compile?"

- **What:** Run `./gradlew compileJava --continue` across all modules. Parse per-module SUCCESS/FAILED results. Diagnose compilation failures by failure class: JDK compatibility errors (wrong Java API version), missing type errors (unresolved transitive deps that slipped through), annotation processor failures, or genuine source errors.
- **How:** The auto-research loop within this stage: (1) Run compilation, capture per-module results. (2) Diagnose failures using the failure taxonomy — JDK_COMPAT errors point to Containerfile changes (switch JDK version), MISSING_DEP errors feed back to Stage 2 (the dependency was resolved but a transitive was missed), COMPILE_ERROR in non-essential modules may warrant exclusion. (3) Apply fix to the appropriate mutable surface. (4) Re-compile and check if more modules pass. Research shows Spring modules have a dependency chain (`spring-jcl` → `spring-core` → `spring-beans` → ...) so a single compilation failure in `spring-core` cascades to 20+ downstream modules — the engine must identify root-cause modules, not count symptoms.
- **Why:** Compilation is the primary gate for the build root's purpose (enabling CVE backporting). Research explicitly recommends distinguishing `compileJava` (compilation only) from `build` (includes tests), and starting with compilation validation before graduating to full builds. The `--continue` flag collects maximum signal even when modules fail.
- **Terminal condition:** `compileJava` succeeds across all included modules (modules excluded in Stage 2 with documented justification are not counted). JARs are produced via `./gradlew jar`.

### Stage 4: TEST — "Do tests pass?"

- **What:** Run `./gradlew test --continue` across all modules. Parse Gradle XML test reports for pass/fail/skip counts per module. Classify test failures: TEST_INFRA (missing external services like databases, message brokers), TEST_ENV (locale/timezone/OS-dependent tests), TEST_TIMEOUT (slow integration tests), and TEST_GENUINE (real test failures indicating a problem).
- **How:** The auto-research loop within this stage: (1) Run tests with `--continue`. (2) Parse XML reports under `build/reports/` for per-test results. (3) Classify failures — TEST_INFRA and TEST_ENV failures are resolved by Containerfile changes (install services, set locale/timezone env vars) or test exclusions. TEST_GENUINE failures are investigated but may warrant exclusion with documentation if they represent pre-existing failures in the historical version. (4) Re-run and check improvement. Research notes that some Spring integration tests are slow and depend on external services — the Containerfile should set standard locale (`en_US.UTF-8`), timezone (`UTC`), and exclude tests that require external infrastructure not present in the container.
- **Why:** Tests validate that the build root produces a functionally correct build, not just a syntactically correct one. However, test pass rate is bounded by what the historical version actually supported — some tests may have been broken at that tag. The goal is passing tests with documented exclusions, not 100% green.
- **Terminal condition:** Tests pass with documented exclusions for environment-dependent tests. Each exclusion has a justification (e.g., "requires running PostgreSQL instance", "timezone-sensitive assertion"). The exclusion list is recorded in the build root's configuration.

## Auto-Research Loop (Per-Stage)

Each stage runs the same loop structure internally:

```
while not terminal_condition_met:
    1. EVALUATE  — run the stage's evaluation command, collect results
    2. DIAGNOSE  — classify failures by type using the failure taxonomy
    3. LOOKUP    — check known-fixes DB for pattern matches; check dead-ends
    4. FIX       — apply known fix, attempt recovery, or use agent reasoning
    5. RECORD    — append new fix or dead-end to known-fixes DB
    6. RE-EVAL   — run evaluation again, check if results improved

    if no improvement for 3 consecutive cycles:
        raise expert gate (plateau)
        wait for human input or apply default action
```

The loop modifies ONLY mutable surfaces. It never touches the project's source code, build definitions, or wrapper configuration.

## Known-Fixes Database with Dead-End Records

- **What:** A YAML database (`config/known-fixes.yaml`) of pattern→fix mappings pre-populated with known failure patterns from research, plus a dead-end registry of confirmed irrecoverable artifacts. Each fix entry specifies a regex pattern, the fix to apply, which mutable surface to modify, and which versions/tags it applies to. Each dead-end entry records artifact coordinates, recovery strategies attempted, confirmation date, and recommended workaround.
- **How:** Two top-level sections: `fixes` and `dead_ends`. Fixes: `{id, pattern, fix_type (init_script|containerfile|build_command|dependency_substitution), fix_content, applies_to (version glob), stage (1-4)}`. Dead-ends: `{artifact (groupId:artifactId:version), strategies_tried, confirmed_date, workaround, confirmed_by (agent|human)}`. Keyed both by artifact coordinates (cross-project reuse) and by project+tag (version-specific knowledge). The agent consults dead-ends BEFORE attempting recovery — known impossibilities are skipped immediately.
- **Why:** Research pre-identified every artifact that breaks Spring builds and its workaround. Starting with this institutional knowledge avoids re-discovering known solutions. The dead-end registry prevents agents from wasting unbounded cycles on artifacts like `propdeps-plugin:0.0.9.RELEASE` (no source repo) or `com.ibm.websphere:uow:6.0.2.17` (proprietary). New discoveries are appended, growing the database across runs.

**Pre-populated entries from research:**

| Pattern | Fix Type | Fix |
|---------|----------|-----|
| `repo.spring.io` 401/403 | init_script | Inject `mavenCentral()` + `gradlePluginPortal()` into all resolution paths |
| `propdeps-plugin:0.0.9.RELEASE` not found | dependency_substitution | `cn.bestwu.gradle:propdeps-plugin:0.0.10` |
| `docbook-reference-plugin:0.3.1` not found | build_command | Add `-x reference -x javadoc` |
| `spring-asciidoctor-extensions` not found | build_command | Add `-x asciidoctor` |
| `com.ibm.websphere:uow` not found | dead_end | Exclude WebSphere module support |

## Expert Gates

Structured human escalation points that pause the pipeline for input:

- **Artifact Recovery Gate:** When artifact recovery fails after exhausting all automated strategies. Prompt: "Cannot find source for `groupId:artifactId:version`. Can I substitute X, or should I exclude the dependent module?"
- **Plateau Gate:** When a stage's auto-research loop shows no improvement for 3 consecutive cycles. Prompt: "Stage N is stuck. Current state: [failures]. Strategies tried: [list]. What should I try next?"
- **Build Review Gate:** When all 4 stages complete successfully. Prompt: "Build root is complete. Here are the contents: [Containerfile, init scripts, exclusions, local-repo contents]. Approve?"

Each gate produces a structured event (`{gate_type, stage, context, question, timestamp}`) that the Factory surfaces in its UI for human review. Gates are synchronous by default — the pipeline pauses until input is received.

## Factory Mode Integration

### Mode Identity

`build-root` is a first-class Factory mode, peer to `build`, `improve`, `research`, and `discover`. It has its own detection logic, configuration schema, event types, and UI.

### Factory Detection (`factory detect`)

The Factory detects build-root mode when `factory.md` contains:

```yaml
mode: build-root
```

Required fields in `factory.md` for build-root mode:

```yaml
mode: build-root
project_repo: <path-or-url>          # The project repository
version_tag: <tag>                    # The version to build (e.g., v5.2.9)
jdk_version: <8|11|17|21>            # JDK for the container (inferred from tag if omitted)
build_system: gradle                 # Build system (gradle for Spring Framework)
known_fixes_path: config/known-fixes.yaml
local_repo_path: local-repo/
```

### Event Types

Build-root mode emits structured events that the Factory logs and the UI consumes:

| Event | Payload | When |
|-------|---------|------|
| `stage.entered` | `{stage: 1-4, name, timestamp}` | Pipeline advances to a new stage |
| `stage.completed` | `{stage: 1-4, name, cycles, timestamp}` | Stage terminal condition met |
| `stage.cycle` | `{stage, cycle_num, before, after, fixes_applied}` | Each iteration of the auto-research loop |
| `dep.resolved` | `{artifact, source}` | A dependency is successfully resolved |
| `dep.recovered` | `{artifact, method, local_repo_path}` | An artifact is rebuilt and installed |
| `dep.dead_end` | `{artifact, strategies_tried, workaround}` | An artifact is confirmed irrecoverable |
| `gate.raised` | `{gate_type, stage, context, question}` | Expert gate triggered |
| `gate.resolved` | `{gate_type, resolution, resolved_by}` | Expert gate answered |
| `build_root.complete` | `{containerfile, local_repo, build_script, evidence}` | All 4 stages pass |

### Stage Progress UI

The Factory UI shows build-root progress as a 4-stage pipeline visualization:

```
┌──────────────┐    ┌──────────────────┐    ┌───────────┐    ┌────────┐
│ DEP RESOLVE  │ →  │ ARTIFACT RECOVERY│ →  │  COMPILE  │ →  │  TEST  │
│  ■■■■■■■■□□  │    │  ■■■■□□□□□□      │    │  ○○○○○○○  │    │ ○○○○○  │
│  cycle 7/∞   │    │  cycle 4/∞       │    │  pending  │    │pending │
│  18/22 deps  │    │  2/4 recovered   │    │           │    │        │
└──────────────┘    └──────────────────┘    └───────────┘    └────────┘
```

Each stage box shows:
- Progress bar (filled = resolved/passing, empty = remaining)
- Current cycle count within that stage
- Key metric (deps resolved, artifacts recovered, modules compiling, tests passing)
- Gate indicators when an expert gate is active (flashing/highlighted)

The UI also shows a scrolling event log of the most recent `stage.cycle` events with before/after deltas.

## Architecture

- **Language/Runtime**: This is a Factory mode extension, implemented in the Factory's existing language/runtime. The mode-specific logic (stage orchestration, evaluation, diagnosis) is implemented as Factory mode handlers. Build evaluation scripts (shell + Python) run inside the container.
- **Framework**: Factory mode protocol — `factory detect`, `factory.md` config, event emission, UI hooks. No additional framework.
- **Data Storage**: YAML for configuration (`known-fixes.yaml`), JSON for per-cycle results and events. A local Maven repository (`local-repo/`) for recovered artifacts. Everything is version-controlled and human-readable.
- **Key Libraries**:
  - `Docker` / `Podman` (CLI) — container builds with JDK-specific `eclipse-temurin` base images (research-recommended Adoptium distribution)
  - `PyYAML` — parse and update known-fixes YAML inside the container
  - `jq` — JSON processing in evaluation scripts
  - `requests` — Maven Central API queries for dependency scanning and artifact search

## User Interface

Users interact with build-root mode through the Factory CLI:

1. **Enter build-root mode**: Configure `factory.md` with `mode: build-root`, `version_tag`, and `project_repo`. Run `factory start`.

2. **Monitor progress**: The Factory UI shows the 4-stage pipeline visualization with per-stage cycle counts, metrics, and gate status. Event log streams `stage.cycle` events with diffs.

3. **Respond to expert gates**: When a gate is raised, the Factory UI presents the structured prompt with context. The user provides input (approve substitution, suggest fix, exclude module) and the pipeline resumes.

4. **Inspect outputs**: On completion, the build root artifacts are in the working directory:
   - `Containerfile` — the container definition
   - `gradle/init.d/*.gradle` — repository and dependency override scripts
   - `local-repo/` — recovered artifacts
   - `build.sh` — the build invocation script with task exclusions
   - `results/evidence/` — build logs, JARs produced, test reports
   - `config/known-fixes.yaml` — updated with any new discoveries

5. **Reuse across versions**: The `known-fixes.yaml` database persists across runs. Building a second version in the same cluster benefits from fixes discovered during the first run.

## Non-Goals (v1)

- **Version clustering / version matrix tracking** — intelligence about which versions to build and in what order happens BEFORE entering build-root mode. Build-root takes a single version tag.
- **Patching Spring source code** — build-root builds the code AS-IS. CVE patch application is a follow-on mode.
- **Spring Boot builds** — Spring Framework only. Spring Boot has separate build infrastructure.
- **Binary reproducibility** — the goal is "does it compile and pass tests," not "does it produce bit-identical JARs."
- **RHEL 8 base images** — v1 uses `eclipse-temurin` (Debian-based). RHEL 8 UBI images are deferred.
- **Offline builds** — v1 requires network access. Dependency cache snapshots for air-gapped use are deferred.
- **Parallel multi-version builds** — v1 builds one version at a time.
- **Weighted composite scoring** — there is no 0-100 blended score. Each stage is pass/fail with its own terminal condition.
- **Full recursive artifact recovery beyond depth 3** — deeply nested dependency chains are escalated via expert gate.

## Open Questions

- **Container runtime**: Docker or Podman? Both are supported by most CI environments. Defaulting to Docker with Podman as a flag.
- **repo.spring.io credentials**: Do we have authenticated access for artifacts genuinely not on Maven Central? If not, those are dead-ends requiring module exclusion.
- **Test timeout budget**: What's the maximum time for Stage 4 (test execution)? Proposed: 60 minutes. Tests not completing within budget are excluded with documentation.
- **Expert gate mode in CI**: Should gates block (synchronous) or log-and-continue with a default action (asynchronous) when running in non-interactive CI environments?

## Research Configuration

### Research Target
- **Objective**: Produce a verified build root for Spring Framework at a given version tag — all 4 stages at terminal condition
- **Metric**: `stage_completed` — the highest stage (1-4) that has reached its terminal condition
- **Target**: 4 (all stages complete)
- **Run Command**: `./build.sh`
- **Result Path**: `results/build-root-status.json`
- **Result Parser**: json
- **Timeout**: 7200

### Mutable Surfaces
```
Containerfile
gradle/init.d/*.gradle
build.sh
scripts/*.sh
scripts/*.py
config/known-fixes.yaml
build-overrides/*.gradle
local-repo/**
results/**
```

### Fixed Surfaces
```
spring-*/src/**
spring-*/build.gradle
build.gradle
settings.gradle
gradle/wrapper/gradle-wrapper.properties
buildSrc/src/**
```

### Research Constraints
- Never modify project source code (`spring-*/src/**`) — the goal is to build code as-is
- Never modify `gradle-wrapper.properties` — the Gradle version is part of the historical build spec
- Prefer Gradle init scripts over patching `build.gradle` or `settings.gradle`
- Each stage must reach its terminal condition before the next stage begins — no skipping
- The known-fixes database must be updated when new fixes or dead-ends are discovered
- Check dead-end records before attempting artifact recovery
- Artifact recovery recursion depth capped at 3
- Expert gates are mandatory at: artifact recovery failure, plateau (3 cycles no improvement), and build root completion

### Cost Budget
- Per-cycle: no external API costs (local container builds + Maven Central queries)
- Total: bounded by container build time (~30 min per Stage 3 cycle, ~60 min per Stage 4 cycle)

## Changes from Prior Draft
- **Replaced single-loop 0-100 scoring with 4-stage gated pipeline** — the prior draft blended dependency resolution, compilation, JAR packaging, test execution, and full build into a weighted composite score. This is wrong because the phases are sequential gates: if deps don't resolve, compilation can't run. The new model has 4 stages, each with a pass/fail terminal condition.
- **Reframed as a Factory mode, not a standalone scripts project** — build-root is now a peer of build, improve, research, discover. Added factory.md schema, factory detect logic, event types, and stage progress UI specification.
- **Removed version clustering and version matrix tracker** — clustering is strategy/intelligence work that happens before entering build-root mode. Build-root takes a single version tag as input.
- **Reduced from 10 features to 4 stages + supporting infrastructure** — the prior draft had Dependency Manifest Scanner, Build Score Evaluator, Build Root Constructor, Failure Diagnosis Engine, Known Fixes Database, Artifact Recovery Engine, Agent Research Loop, Expert Gates, Version Matrix Tracker, and Factory Integration as separate features. The new spec organizes these into 4 pipeline stages with the supporting pieces (known-fixes, expert gates, artifact recovery) woven into the stages where they're used.
- **Clarified concrete input/output** — input is repo + version tag, output is Containerfile + local-repo + build script + green build evidence.
- **Removed Post-Clustering Gate** from expert gates — there is no clustering in build-root mode.
