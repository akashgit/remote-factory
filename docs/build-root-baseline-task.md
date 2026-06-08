# Build-Root Baseline Task: Spring Framework 4.x

## What Is This Task

### Build-Root: Verified Build Environment Construction

Build-root is a Factory mode that takes a historical Java project at a specific version tag and produces a **verified build root** — a Containerfile, Gradle init scripts, local Maven repository, and build script that together produce a green build from unmodified historical source. The source code is never modified; all build behavior changes go through Gradle init scripts and container configuration.

The purpose: enable CVE backporting to old, unsupported versions. You can't patch a vulnerability in code you can't compile.

### The Target: Spring Framework 4.x

Spring Framework 4.x is the ideal baseline challenge:

- **End of life.** The 4.3.x line was the last 4.x release. Spring 4.3.30 shipped September 2020. OSS support ended December 31, 2020. Extended commercial support (VMware) ended December 31, 2024. The 4.x series receives no patches of any kind.
- **Infrastructure has rotted.** `repo.spring.io` — the primary artifact repository for Spring builds — now returns 401/403 for many paths that 4.x builds depended on. Plugins referenced in 4.x build scripts have been removed from plugin portals. Transitive dependencies have been pulled from public repositories.
- **Still deployed in production.** Large enterprises run Spring 4.x in production systems that can't be upgraded due to compatibility constraints, regulatory freezes, or resource limitations. When a critical CVE drops, they need to patch 4.x — but they can't build it.
- **Cascading module dependencies.** Spring Framework has ~20 modules with strict ordering: `spring-jcl → spring-core → spring-beans → spring-context → ...`. A single root-cause failure (e.g., `spring-jcl` can't resolve a dependency) cascades to every downstream module. The build-root pipeline must identify root causes, not count symptoms.

**Recommended starting version: `v4.3.30.RELEASE`** — the final 4.3.x release. This is the version most likely to be in production and most likely to need a CVE backport. It uses Gradle 4.x (not 5.x+), JDK 8, and depends on the full set of now-rotted infrastructure.

Alternative versions to try after baseline:
- `v4.3.0.RELEASE` — earliest 4.3.x, tests whether fixes generalize across the minor line
- `v4.2.9.RELEASE` — last 4.2.x, different Gradle version, different dependency set
- `v4.0.9.RELEASE` — oldest supported 4.x, maximum infrastructure rot

## Challenges (By Design)

These are the failure modes the build-root pipeline was specifically designed to handle. Each one was identified during research and encoded into the known-fixes database and agent prompt.

### 1. repo.spring.io 401/403 Cascade

**What happens:** Spring 4.x `build.gradle` files point exclusively to `repo.spring.io` for dependency resolution. That repository now returns 401 Unauthorized or 403 Forbidden for most artifact paths. Every dependency resolution attempt fails.

**Why it's hard:** This isn't one missing artifact — it's the entire resolution infrastructure. Gradle tries `repo.spring.io` first (because it's listed first in the project's `build.gradle`), fails, and doesn't fall back because no fallback repositories are configured.

**The fix:** Gradle init script (`repositories.gradle`) that injects `mavenCentral()`, `gradlePluginPortal()`, and `mavenLocal()` into **three** resolution paths:
1. `allprojects.repositories` — standard dependency resolution
2. `allprojects.buildscript.repositories` — buildscript/plugin classpath resolution  
3. `settingsEvaluated.pluginManagement.repositories` — plugin DSL resolution (Gradle 4.1+)

Missing any one of these three paths causes a different class of 401 error. This is the single most common failure in historical Spring builds and is pre-populated in `config/known-fixes.yaml` as `spring-repo-401`.

### 2. Deprecated Plugin: propdeps-plugin 0.0.9

**What happens:** Spring 4.x uses `org.springframework.build.gradle:propdeps-plugin:0.0.9` for optional dependency management. This plugin is no longer published to any public repository.

**Why it's hard:** The plugin is referenced in `buildscript { dependencies { classpath ... } }` — this is the **buildscript** classpath, not the project classpath. Standard `repositories {}` overrides don't help. The fix requires either a dependency substitution via `resolutionStrategy.eachDependency` in a Gradle init script, or an alternative plugin.

**The fix:** Substitution init script (`substitutions.gradle`) that remaps `org.springframework.build.gradle:propdeps-plugin:0.0.9` → `cn.bestwu.gradle:propdeps-plugin:0.0.10`. The Bestwu fork is a maintained drop-in replacement published to Maven Central. Pre-populated as `propdeps-substitute` in known-fixes.

### 3. Defunct Documentation Plugins

**What happens:** Spring 4.x builds reference `docbook-reference-plugin` and `spring-asciidoctor-extensions` for documentation generation. These plugins and their transitive dependencies are no longer available.

**Why it's hard:** These failures block the entire build because Gradle resolves all plugins before executing any task — even if you're only running `compileJava`.

**The fix:** Task exclusions in the build command: `-x reference -x javadoc -x asciidoctor`. These skip documentation tasks entirely without affecting compilation or tests. Pre-populated as `docbook-exclude` and `asciidoctor-exclude` in known-fixes.

### 4. Proprietary Artifacts (Dead Ends)

**What happens:** Some Spring modules depend on proprietary artifacts that were never in public repositories — `com.ibm.websphere:uow:6.0.2.17` (IBM WebSphere UOW API), Oracle JDBC drivers, etc.

**Why it's hard:** No amount of searching will find these artifacts. The build-root pipeline must recognize dead ends and exclude the dependent modules rather than cycling indefinitely.

**The fix:** Dead-end registry in `config/known-fixes.yaml`. Before attempting artifact recovery, check the dead-end list. If an artifact is marked dead, exclude the dependent module and document the exclusion with justification. The pipeline has a recursion depth cap of 3 recovery attempts per artifact — after 3 failures, it's marked as dead.

### 5. Gradle Version Compatibility

**What happens:** Spring 4.x ships with Gradle 4.x wrappers. Gradle 4.x has different init script behavior than Gradle 5+/6+/7+:
- `settingsEvaluated` callback may not support `pluginManagement.repositories` in Gradle 4.x (feature added in 4.1)
- `resolutionStrategy.eachDependency` syntax may differ
- Cache layout is incompatible between major Gradle versions

**Why it's hard:** Init scripts that work for Spring 5.x (Gradle 5+) may not work for Spring 4.x (Gradle 4.x). The pipeline must adapt.

**The fix:** The build-root-ceo agent diagnoses Gradle version-specific failures and adjusts init scripts. The known-fixes database supports `applies_to` version globs (e.g., `v4.3.*` vs `v5.2.*`) for version-specific fixes.

### 6. JDK Version Sensitivity

**What happens:** Spring 4.x requires JDK 8. Running it with JDK 11+ causes compilation errors from removed APIs (`javax.xml.bind`, `javax.annotation`, etc.) and changed module system behavior.

**The fix:** The `BuildRootConfig.jdk_version` field controls the container base image. For Spring 4.x, set `jdk_version: 8` to use `docker.io/library/eclipse-temurin:8-jdk-jammy`.

### 7. Test Infrastructure Dependencies

**What happens:** Spring integration tests depend on external services — ActiveMQ (JMS), OpenLDAP, PostgreSQL, embedded databases, SMTP servers. These aren't available in the container.

**Why it's hard:** The test failures look like code bugs but are actually missing infrastructure. The pipeline must classify failures correctly:
- `TEST_INFRA`: ConnectionRefused, database drivers, JMS, LDAP → exclude with justification
- `TEST_ENV`: Locale/timezone in assertions → fix via container env vars
- `TEST_TIMEOUT`: Tests exceeding 60s → exclude (CI timing sensitivity)
- `TEST_GENUINE`: Everything else → investigate

**The fix:** Test classification in `scripts/parse_test_reports.py` and documented exclusions in `results/test-exclusions.json`.

## Practical Infrastructure Setup

### Disk Space (Critical)

This is the #1 operational issue. Large Java builds consume significant disk:

| Component | Size |
|-----------|------|
| Container base image (eclipse-temurin JDK) | ~400 MB |
| Container build layers | 1-2 GB |
| Gradle distribution download | ~100 MB |
| Gradle dependency cache | 5-10 GB |
| Build outputs (per module × 20 modules) | 1-3 GB |
| **Total per build root** | **~8-16 GB** |

**The problem:** Default Podman storage lives on the root partition (`~/.local/share/containers/`). Many cloud VMs have a small root partition (100GB) that fills up fast. This machine has 21GB free on root — barely enough for one build.

**The fix:** Set `PODMAN_STORAGE_ROOT` to redirect container storage to a large drive:

```bash
export PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage
```

`build.sh` passes `--root $PODMAN_STORAGE_ROOT` to all `podman build` and `podman run` commands. It also checks disk space at startup and warns if < 20GB is available.

On this machine, 8× 7TB NVMe drives are available at `/mnt/nvme{0-7}n1/`. Use `/mnt/nvme7n1` (6.6TB free, 1% used) for Podman storage.

### Podman Configuration

```bash
# Verify Podman is installed
podman --version  # needs >= 4.0

# Podman rootless requires subuid/subgid entries
grep $(whoami) /etc/subuid  # should have an entry
grep $(whoami) /etc/subgid  # should have an entry

# If using custom storage root, create the directory
mkdir -p /mnt/nvme7n1/podman-storage

# Verify the prerequisites script
./scripts/check-prerequisites.sh
```

**Short-name resolution:** Podman enforces fully qualified image names. The Containerfile uses `docker.io/library/eclipse-temurin:8-jdk-jammy` (not just `eclipse-temurin:8-jdk-jammy`). This was a real bug we caught during container build testing — Podman can't prompt for registry selection without a TTY.

### Build Modes (Fast vs Full)

The build script supports three modes for different phases of the build-root pipeline:

| Mode | Command | Time | When to Use |
|------|---------|------|-------------|
| `compile` | `compileJava` | ~2-3 min | Early iteration, stage 3 default |
| `fast` | `compileJava + compileTestJava` | ~3-5 min | Auto-research loop iteration |
| `full` | `clean test build` | ~20-40 min | Final verification after stage completes |

```bash
# During iteration (fast feedback loop)
BUILD_MODE=fast BUILD_STAGE=3 ./build.sh

# Final verification (once, after stage passes)
BUILD_MODE=full BUILD_STAGE=3 ./build.sh
```

**Why this matters:** If you're iterating 10-20 times to fix missing artifacts, each iteration at 3 minutes = 30-60 minutes total. Each iteration at 30 minutes = 5-10 hours. The fast/full distinction is the difference between finishing in an hour and finishing in a day.

### Network Access

The pipeline needs network access for:
- Pulling the container base image from Docker Hub (`docker.io/library/eclipse-temurin`)
- Resolving dependencies from Maven Central (`repo1.maven.org`)
- Downloading the Gradle distribution (first run only)
- Web searches for artifact recovery (via Researcher agent)

Air-gapped builds are explicitly a non-goal for v1.

### Running the Baseline

```bash
# 1. Configure factory.md with build-root settings
cat >> factory.md << 'EOF'

## Build Root
- project_repo: https://github.com/spring-projects/spring-framework
- version_tag: v4.3.30.RELEASE
- jdk_version: 8
- build_system: gradle
- known_fixes_path: config/known-fixes.yaml
- local_repo_path: local-repo/
EOF

# 2. Set up storage
export PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage
mkdir -p $PODMAN_STORAGE_ROOT

# 3. Verify prerequisites
./scripts/check-prerequisites.sh

# 4. Run build-root mode
factory ceo /path/to/project --mode build-root
```

### Expected Pipeline Progression

For Spring Framework v4.3.30.RELEASE, expect roughly:

| Stage | Expected Cycles | Expected Outcome |
|-------|----------------|------------------|
| 1. DEP RESOLVE | 5-10 | Most deps resolve via mavenCentral() after init script injection. A few need substitutions. |
| 2. ARTIFACT RECOVERY | 3-5 | Most artifacts available on Maven Central under different coordinates. 1-2 dead ends (proprietary). |
| 3. COMPILE | 3-7 | Cascade failures from root modules. Fix spring-jcl/spring-core first. Exclude docbook/asciidoctor modules. |
| 4. TEST | 5-10 | Heavy TEST_INFRA exclusions (JMS, LDAP, databases). Some TEST_ENV fixes (locale). Goal: pass with documented exclusions. |

Total expected time: 2-4 hours (with fast build mode), not counting expert gate wait times.

### Success Criteria

The baseline is successful when:
1. `results/build-root-status.json` shows `stage_completed: 3` or higher (compilation passes)
2. All exclusions are documented with justifications in `results/module-exclusions.json` and `results/test-exclusions.json`
3. The git log shows a clean audit trail: every fix committed, every failed fix reverted
4. The known-fixes database has been updated with any new discoveries
5. The build root can be re-run from scratch and reach the same state (reproducibility)

Stage 4 (tests) with documented exclusions is the stretch goal. Stage 3 (compilation) is the primary gate — a compilable build root is sufficient for CVE backporting.
