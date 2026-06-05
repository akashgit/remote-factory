# Build-Root Mode

Build-root mode produces **verified build environments** for historical Java project versions. Given a repository URL and a version tag, it checks out the source, constructs a containerized build root (Containerfile, Gradle init scripts, local Maven repository overrides), and drives a 4-stage pipeline until the project compiles and tests pass — or an expert gate blocks progress.

The pipeline never modifies project source code. All build behavior changes go through Gradle init scripts and container configuration.

## System Requirements

| Requirement | Minimum |
|---|---|
| OS | Linux (primary). macOS works but Podman requires a Linux VM (`podman machine`). |
| RAM | 8 GB |
| Disk | 20 GB free (container images + Gradle caches) |

## Required Tools

### Podman >= 4.0

Build-root runs Gradle inside OCI containers. Podman is the default runtime.

```bash
# Ubuntu / Debian
sudo apt install podman

# Fedora / RHEL
sudo dnf install podman

# macOS
brew install podman
podman machine init
podman machine start
```

Verify: `podman --version`

### Python >= 3.11

The Factory CLI and helper scripts require Python 3.11+.

Verify: `python3 --version`

### git >= 2.30

Used for cloning the target project and maintaining the audit trail of fixes.

Verify: `git --version`

## Optional Tools

- **Docker** — Set `CONTAINER_RUNTIME=docker` to use Docker instead of Podman.
- **JDK** — Install a local JDK matching `jdk_version` for debugging outside containers.

## Quick Start

```bash
# 1. Install factory dependencies
uv sync

# 2. Verify prerequisites
./scripts/check-prerequisites.sh

# 3. Add a ## Build Root section to your project's factory.md:
#
#    ## Build Root
#    - project_repo: https://github.com/spring-projects/spring-framework
#    - version_tag: v5.2.9.RELEASE
#    - jdk_version: 11
#    - build_system: gradle

# 4. Run build-root mode
factory ceo /path/to/project --mode build-root
```

## Configuration Reference

Add a `## Build Root` section to your project's `factory.md`. The factory parses it into a `BuildRootConfig` model.

| Field | Type | Default | Description |
|---|---|---|---|
| `project_repo` | string | **(required)** | Git repository URL for the target project. |
| `version_tag` | string | **(required)** | Git tag to check out (e.g. `v5.2.9.RELEASE`). |
| `jdk_version` | int | `11` | JDK major version. Supported: 8, 11, 17, 21. |
| `build_system` | string | `"gradle"` | Build system. Currently only `gradle` is supported. |
| `known_fixes_path` | string | `"config/known-fixes.yaml"` | Path to the known-fixes database. |
| `local_repo_path` | string | `"local-repo/"` | Path to the local Maven repository for recovered artifacts. |

Example `factory.md` section:

```markdown
## Build Root
- project_repo: https://github.com/spring-projects/spring-framework
- version_tag: v5.2.9.RELEASE
- jdk_version: 11
- build_system: gradle
- known_fixes_path: config/known-fixes.yaml
- local_repo_path: local-repo/
```

## Pipeline Stages

Build-root runs a strict 4-stage pipeline. Each stage has an eval command, terminal condition, and failure taxonomy. Stages execute in order — no skipping or reordering.

### Stage 1: DEP RESOLVE

Resolves all Gradle dependencies inside the container.

- **Eval command:** `./gradlew dependencies --configuration compileClasspath --continue`
- **Terminal condition:** 0 FAILED dependencies
- **Common failures:** 401/403 from defunct repositories (fix: repo override via init script), missing plugins (fix: dependency substitution), version conflicts (fix: force resolution)

### Stage 2: ARTIFACT RECOVERY

Recovers artifacts that cannot be resolved through repository overrides. Uses web search and manual download into `local-repo/`.

- **Terminal condition:** All recoverable artifacts present in `local-repo/`, remaining artifacts registered as dead ends
- **Recursion depth cap:** 3 attempts per artifact
- **Dead-end check:** Always consults `known-fixes.yaml` dead-end registry before attempting recovery

### Stage 3: COMPILE

Compiles all project modules inside the container.

- **Eval command:** `./gradlew compileJava --continue`
- **Terminal condition:** All included modules compile successfully
- **Common failures:** Cascade failures from missing dependencies (e.g. spring-jcl causing spring-core failures). Irrecoverable modules are excluded via Gradle task exclusion.

### Stage 4: TEST

Runs the project test suite inside the container.

- **Eval command:** `./gradlew test --continue`
- **Terminal condition:** Tests pass with documented exclusions in `results/test-exclusions.json`
- **Failure classification:** TEST_INFRA (missing services), TEST_ENV (locale/timezone), TEST_TIMEOUT (>60s), TEST_GENUINE (real failures)

## Known Fixes Database

The file at `config/known-fixes.yaml` stores reusable fixes and dead ends. The build-root agent consults this database before attempting any repair.

### Schema

```yaml
version: 1

universal:
  fixes: []
  dead_ends: []

projects:
  <project-name>:
    fixes:
      - id: <unique-id>
        pattern: '<regex matching Gradle error output>'
        fix_type: init_script | substitution | build_command
        fix_content: '<fix payload>'
        applies_to: '<version glob, e.g. v5.2.*>'
        stage: <1-4>
    dead_ends:
      - artifact: '<groupId:artifactId:version>'
        reason: '<why recovery is impossible>'
        workaround: '<alternative approach>'
        applies_to: '<version glob>'
```

### Fix types

| Type | Description |
|---|---|
| `init_script` | Gradle init script content injected into `gradle/init.d/` |
| `substitution` | Dependency substitution rule (e.g. `group:old-artifact` to `group:new-artifact:version`) |
| `build_command` | Additional flags appended to the Gradle command (e.g. `-x reference`) |

### Adding new entries

1. Identify the error pattern from Gradle output.
2. Write a regex that matches the pattern.
3. Add a fix entry under the appropriate project (or `universal` for cross-project fixes).
4. Set `applies_to` to the narrowest version glob that covers the affected versions.
5. Set `stage` to the pipeline stage where this fix applies.

The agent also appends new discoveries automatically during the pipeline run.

### Lookup order

1. Project-specific fixes (matched by project name)
2. Universal fixes
3. Dead-end registry (checked before attempting artifact recovery)

## Expert Gates

Expert gates pause the pipeline and request human input. Three gate types exist:

### Artifact Recovery Gate

Triggered when an artifact cannot be found after exhausting automated strategies.

> Cannot find source for `groupId:artifactId:version`. Strategies tried: [...]. Can I substitute X, or should I exclude the dependent module?

### Plateau Gate

Triggered when a stage makes no progress after 3 consecutive cycles.

> Stage N stuck after K cycles (M minutes elapsed). Current state: [metrics]. What should I try next?

### Build Review Gate

Triggered when the pipeline completes all 4 stages.

> Build root complete. Contents: [summary]. Approve?

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CONTAINER_RUNTIME` | `podman` | Container runtime executable (`podman` or `docker`) |
| `BUILD_STAGE` | — | Pipeline stage number (1-4), set by `build.sh` |

## Project Layout

After a build-root run, the worktree contains:

```
Containerfile                     # Generated container image definition
build.sh                         # Build orchestration script
gradle/init.d/
  repositories.gradle            # Repository override init script
  substitutions.gradle           # Dependency substitution init script
config/
  known-fixes.yaml               # Known fixes and dead ends database
local-repo/                      # Recovered Maven artifacts
results/
  build-root-status.json         # Current pipeline state
  test-exclusions.json           # Documented test exclusions
scripts/
  parse_gradle_deps.py           # Dependency resolution output parser
  parse_compile_results.py       # Compilation output parser
  parse_test_reports.py          # Test report parser and classifier
  generate_containerfile.py      # Containerfile generator
```
