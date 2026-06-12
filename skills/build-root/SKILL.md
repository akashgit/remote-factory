---
name: build-root
description: "Build a verified build root for a historical Java package. Reconstructs dead build infrastructure, matches the original JDK/compiler, runs tests, and verifies equivalence against Maven Central originals. Use when the user says 'build root for X', 'rebuild X from source', or wants to compile an old Java package for CVE backporting."
disable-model-invocation: true
argument-hint: "<group:artifact:version> e.g. org.springframework:spring-framework:3.0.0.RELEASE"
---

# /factory:build-root

Build a verified build root for a historical Java package.

**Target:** $ARGUMENTS

## What This Skill Does

Constructs a containerized build environment that compiles a historical Java project from unmodified source, producing JARs verified against the originals from Maven Central. The source code is never modified — all fixes go through build system configuration, dependency remapping, and container setup.

## Prerequisites

```bash
# Podman (container runtime)
podman --version  # needs >= 4.0

# Storage on a large drive (builds need 8-16GB)
export PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage  # adjust path
mkdir -p $PODMAN_STORAGE_ROOT

# Verify rootless podman
grep $(whoami) /etc/subuid
grep $(whoami) /etc/subgid
```

## The Process

Execute these phases in order. Each phase has a verification gate — do NOT proceed until the gate passes.

---

### Phase 1: Research the Target

**Goal:** Understand the original build setup completely before writing any code.

**Step 1.1 — Identify build system and toolchain:**
```bash
# Check the project's build files on GitHub
# For each file, note: exists (200) or not (404)
TARGET_REPO="<github-org>/<repo>"
TARGET_TAG="<version-tag>"

# Gradle?
curl -sI "https://raw.githubusercontent.com/$TARGET_REPO/$TARGET_TAG/build.gradle" | head -1
curl -sI "https://raw.githubusercontent.com/$TARGET_REPO/$TARGET_TAG/gradle/wrapper/gradle-wrapper.properties" | head -1

# Maven?
curl -sI "https://raw.githubusercontent.com/$TARGET_REPO/$TARGET_TAG/pom.xml" | head -1

# Ant+Ivy?
curl -sI "https://raw.githubusercontent.com/$TARGET_REPO/$TARGET_TAG/build.xml" | head -1
curl -sI "https://raw.githubusercontent.com/$TARGET_REPO/$TARGET_TAG/ivy.xml" | head -1
```

Record:
- Build system: Gradle (version from wrapper) / Maven / Ant+Ivy
- JDK version: from `sourceCompatibility`, `maven.compiler.source`, or project docs
- Compiler: javac (default) or Eclipse JDT (check for `build.compiler=org.eclipse.jdt`)
- Repositories: list every URL in build files, check which return 200 vs 401/403/404

**Step 1.2 — Check dependency availability:**
```bash
# For each dependency in the build file, verify on Maven Central
curl -sI "https://repo1.maven.org/maven2/<group-path>/<artifact>/<version>/<artifact>-<version>.jar" | head -1
```

Classify each dependency:
- **AVAILABLE:** resolves from Maven Central
- **AVAILABLE_ELSEWHERE:** not on Maven Central but found on other public repos (Red Hat Maven, JBoss, Gradle Plugin Portal)
- **NEEDS_SUBSTITUTE:** not available anywhere but a fork/replacement exists
- **NEEDS_STUB:** not available, no substitute — create a minimal stub JAR
- **DEAD_END:** proprietary, no public source — exclude the dependent module

**Step 1.3 — Find the exact JDK:**
```bash
# Check for Docker images of the required JDK version+vendor
# IMPORTANT: Use the SAME JDK vendor as the original build, not just the same version

# Sun/Oracle JDK (proprietary — community images may exist)
podman pull docker.io/dingmingk/java-jdk6-oracle  # Sun JDK 6
# No official Oracle images — search community repos

# OpenJDK (various vendors)
podman pull docker.io/azul/zulu-openjdk:7          # Zulu JDK 7
podman pull docker.io/library/eclipse-temurin:8-jdk-jammy  # Temurin JDK 8

# Verify it works
podman run --rm <image> java -version
```

**WHY THIS MATTERS:** Different JDK vendors produce different bytecode for the same source. We proved this empirically:
- Zulu OpenJDK 6 vs Sun JDK 6: 6 test failures from MBeanServer behavioral differences
- JDK 8 vs JDK 7: SHA-256 dropped from 100% to 43.7%
- Matching the exact JDK vendor+version gives 99.6%+ SHA-256

**Step 1.4 — Find the exact compiler:**
```bash
# If the project uses Eclipse JDT instead of javac:
curl -sI "https://repo1.maven.org/maven2/org/eclipse/jdt/core/<version>/core-<version>.jar" | head -1

# If it uses a specific javac version, that comes with the JDK image
```

**GATE:** You must know: build system, Gradle/Maven/Ant version, JDK version+vendor, compiler, every dead repository, every unavailable dependency. If any of these are unknown, research more before proceeding.

---

### Phase 2: Build the Scaffold

**Goal:** Create the containerized build environment.

The scaffold depends on the build system:

#### For Gradle projects:

Create these files:
```
<version>/
├── Containerfile              # JDK + build tools
├── build.sh                   # Podman orchestrator (image/run/clean)
├── gradle-init/
│   └── repo-override.gradle  # Repository interception init script
├── local-repo/               # Repackaged/stub JARs
├── scripts/
│   ├── entrypoint.sh         # Clone, build, test, report
│   └── check-prerequisites.sh
├── config/
│   └── known-fixes.yaml      # Documented fixes
├── .gitignore                # Exclude results/test-reports/, results/build.log
└── results/                  # Build output (gitignored)
```

**Init script pattern (adapt for Gradle version):**
```groovy
// For Gradle 4.1+:
apply plugin: RepoOverridePlugin
class RepoOverridePlugin implements Plugin<Gradle> {
    void apply(Gradle gradle) {
        def configureRepos = { repos ->
            repos.clear()
            repos.mavenLocal()
            repos.mavenCentral()
            repos.maven { url "https://plugins.gradle.org/m2/" }
        }
        gradle.allprojects { project ->
            project.repositories configureRepos
            project.buildscript.repositories configureRepos
        }
        gradle.settingsEvaluated { settings ->
            settings.pluginManagement.repositories configureRepos
        }
    }
}

// For Gradle 2.x-4.0: REMOVE settingsEvaluated block and gradlePluginPortal()
// Replace gradlePluginPortal() with: maven { url "https://plugins.gradle.org/m2/" }
```

**CRITICAL RULES for init scripts:**
1. Place in `~/.gradle/init.d/` (NOT `-I` flag — doesn't cover buildSrc)
2. Use `repos.clear()` to remove dead repos before adding working ones
3. `gradlePluginPortal()` only works in Gradle 4.1+ — use explicit URL for older versions
4. `settings.pluginManagement` only works in Gradle 4.1+ — remove for older versions
5. For Gradle 2.x: add `project.afterEvaluate { project.repositories configureRepos }` to catch late-added repos

**JDK version determines the Containerfile base image AND what's possible:**

| JDK | Base Image | TLS Status | Network Strategy |
|-----|-----------|-----------|-----------------|
| JDK 8+ | `eclipse-temurin:8-jdk-jammy` | Works | Direct — init scripts redirect repos |
| JDK 7 | `azul/zulu-openjdk:7` | **BROKEN** — can't connect to HTTPS | Two-stage: curl prefetch → offline build |
| JDK 6 | `azul/zulu-openjdk:6` or `dingmingk/java-jdk6-oracle` | **BROKEN** | Two-stage: curl prefetch → offline build |

**For JDK 7 and below — two-stage build:**
```bash
# Stage 1: prefetch-deps.sh downloads everything with curl (system OpenSSL, handles TLS 1.2)
curl -fsSL -o "$LOCAL_REPO/$GROUP_PATH/$ARTIFACT/$VERSION/$ARTIFACT-$VERSION.jar" \
    "https://repo1.maven.org/maven2/$GROUP_PATH/$ARTIFACT/$VERSION/$ARTIFACT-$VERSION.jar"

# Stage 2: Gradle/Ant runs offline from mavenLocal only
# Init script: repos.clear() + repos.mavenLocal() — nothing else
```

#### For Ant+Ivy projects:

Create these files:
```
<version>/
├── Containerfile
├── build.sh
├── config/
│   ├── ivysettings.xml        # Replacement Ivy settings (ibiblio → Maven Central)
│   ├── ebr-mapping.json       # EBR-to-Maven coordinate mapping (if using EBR names)
│   └── known-fixes.yaml
├── scripts/
│   ├── entrypoint.sh
│   ├── rewrite-ivy.py         # Automated ivy.xml rewriter (if EBR deps)
│   ├── prefetch-deps.sh       # curl-based dependency prefetch
│   └── create-stubs.sh        # Stub JAR generator for proprietary deps
├── .gitignore
└── results/
```

**ivysettings.xml replacement:**
```xml
<ivysettings>
    <settings defaultResolver="main-chain"/>
    <resolvers>
        <chain name="main-chain" returnFirst="true">
            <ibiblio name="maven-central" m2compatible="true" usepoms="false"
                     root="https://repo1.maven.org/maven2"/>
        </chain>
    </resolvers>
</ivysettings>
```

**CRITICAL: `usepoms="false"`** prevents Maven from pulling transitive dependencies through EBR-named artifacts that don't exist on Maven Central.

#### For Maven projects:

Create `settings.xml` with mirror overrides:
```xml
<settings>
    <mirrors>
        <mirror>
            <id>central-override</id>
            <mirrorOf>*</mirrorOf>
            <url>https://repo1.maven.org/maven2</url>
        </mirror>
    </mirrors>
</settings>
```

**GATE:** The container image must build (`podman build`). If it fails, fix before proceeding.

---

### Phase 3: Iterate Compilation

**Goal:** Get `compileJava` (or equivalent) to exit 0 across all modules.

```bash
# Build the image
PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage ./build.sh image

# Run compile-only (fast feedback: ~2-3 min)
PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage BUILD_MODE=compile ./build.sh run
```

**Expect 10-25 iterations.** Each iteration:
1. Read the FIRST error in the output (not the last — cascading failures mislead)
2. Diagnose: missing dependency? Wrong repo? Plugin ID mismatch? Gradle API incompatibility?
3. Fix the root cause (not the symptom)
4. Rebuild image + re-run
5. Document the fix in `known-fixes.yaml`

**Common failure patterns and fixes:**

| Error Pattern | Root Cause | Fix |
|---------------|-----------|-----|
| `Could not resolve <artifact>` | Dead repository | Init script repo redirect |
| `Plugin with id '<name>' not found` | Plugin JAR missing OR plugin ID mismatch | Repackage JAR with correct plugin ID descriptors |
| `Could not find method reference()` | Stub plugin doesn't register extension | Add extension class with all properties |
| `gradlePluginPortal() not found` | Gradle version < 4.1 | Use explicit URL |
| `pluginManagement not found` | Gradle version < 4.1 | Remove from init script |
| `peer not authenticated` | JDK 7 can't do TLS 1.2 | Two-stage curl prefetch |
| `UnsupportedClassVersionError` | Stub compiled with newer JDK | Recompile stub with target JDK |
| `warnings found and -Werror specified` | Plugin substitution causes deprecation warnings | Suppress warnings or adjust init script |
| `Task '<name>' not found` | `-x` flag references non-existent task | Remove the flag for this version |

**GATE:** `compileJava` (or `ant compile`) exits 0 for all modules.

---

### Phase 4: Run Tests

**Goal:** Execute the project's own test suite and report actual numbers.

```bash
PODMAN_STORAGE_ROOT=/mnt/nvme7n1/podman-storage BUILD_MODE=test ./build.sh run
cat results/build-root-status.json
```

**MANDATORY:** Report actual test counts — total, passed, failed, errors, skipped. NEVER claim tests pass without running them.

**Container configuration for tests:**
```bash
# Raise thread/pid limits for concurrent tests
podman run --ulimit nproc=65535:65535 --pids-limit=-1 ...
```

**Classify every failure:**
- **MISSING_DEP:** Fixable — add the dependency to the mapping/prefetch
- **JDK_COMPAT:** Different JDK version/vendor — match the original to fix
- **INFRA:** Needs external services (DB, MQ, LDAP) — document and exclude
- **GENUINE_BUG:** Real bug in the original code — would fail on any build
- **INTENTIONAL:** Test designed to fail (validates error handling)

**Iterate:** Fix MISSING_DEP and JDK_COMPAT failures. Document the rest.

**GATE:** Tests run with reported numbers. All fixable failures are fixed.

---

### Phase 5: Equivalence Test

**Goal:** Compare rebuilt JARs against originals from Maven Central.

```bash
# Use the equivalence test tool from ai-companion/java-build-equivalence-test
# Three tools, from weakest to strongest:

# Tool 1: japicmp — binary API compatibility
java -jar japicmp.jar --old original.jar --new rebuilt.jar --ignore-missing-classes

# Tool 2: Class inventory — same .class files present?
jar tf original.jar | grep '\.class$' | sort > orig.txt
jar tf rebuilt.jar | grep '\.class$' | sort > ours.txt
diff orig.txt ours.txt

# Tool 3: SHA-256 per .class file — byte-identical?
# Extract both JARs, hash each .class file, compare
```

**Expected results by JDK match quality:**

| JDK Match | Expected SHA-256 |
|-----------|-----------------|
| Same vendor + same major version | 99-100% |
| Same major version, different vendor | 95-99% |
| Different major version | 40-95% |

**GATE:** japicmp says COMPATIBLE for all modules. Class inventory matches. SHA-256 score documented.

---

### Phase 6: Document and Ship

**For each build, document in the PR:**

1. **Design decisions table:** What was original vs what we used, with match status
2. **Substitution inventory:** Every artifact we replaced, with risk assessment
3. **Test results:** Actual numbers with failure classifications
4. **Equivalence results:** japicmp, class inventory, SHA-256 per module
5. **Known issues:** What's left unfixed and why

**PR structure:**
- One branch per version
- Each PR targets the previous version's branch (for reviewable diffs)
- Only infrastructure files committed — NEVER test reports or build logs
- BUILD-REPORT.md in each branch with all results

---

## Anti-Patterns (Mistakes We Actually Made)

1. **Claiming tests pass without running them.** The most dangerous mistake — caught by user review.
2. **Accepting "unavailable" without searching.** JMXMP and Eclipse JDT were both on Maven Central — we just didn't look.
3. **Using "compatible" JDK instead of exact.** Zulu≠Sun, JDK 8≠JDK 7. Compatibility doesn't mean identical.
4. **Using "compatible" compiler.** javac≠Eclipse JDT. Different compilers produce different bytecode.
5. **Committing build artifacts to git.** 1,692 test XMLs made the PR unreviewable.
6. **Stubbing when the real artifact exists.** Always search Maven Central, Red Hat Maven, JBoss, GlassFish repos first.
7. **Hardcoding version-specific flags.** `-x reference` works on 4.x but crashes 5.x.
8. **Not comparing against originals.** Build success ≠ correctness.

## Connecting to Auto-Research

This skill works with the Factory's research pipeline:

```bash
# The Researcher agent handles Phase 1 automatically
factory agent researcher --task "Research build setup for <package> <version>" --project .

# The Builder agent handles Phases 2-4
factory agent builder --task "Build and test <package> <version>" --project .

# The equivalence test tool handles Phase 5
factory build-root-compare --group <group> --modules <modules> --version <version> --rebuilt-dir <dir>
```

The known-fixes database (`config/known-fixes.yaml`) accumulates institutional knowledge across builds. Fixes discovered for one version often apply to others in the same project.
