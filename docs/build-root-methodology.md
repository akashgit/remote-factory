# Build Root: Methodology for Rebuilding Ancient Software Packages

## What Is a Build Root?

A build root is a self-contained, containerized environment that can compile a historical software project from unmodified source code — even when the original build infrastructure no longer exists. The source code is never modified; all build behavior changes go through build system configuration, dependency remapping, and container setup.

The purpose: enable CVE backporting to old, unsupported versions. You can't patch a vulnerability in code you can't compile.

## The Hindsight Process

After building Spring Framework v4.3.30.RELEASE (Gradle, 2020) and v3.0.0.RELEASE (Ant+Ivy, 2009) from source, here's the idealized process distilled from what actually worked — and what we had to learn the hard way.

### Phase 1: Infrastructure Audit

Before writing any code, answer these questions:

**1. What build system does the project use?**
- Gradle: you have init scripts (can intercept without touching source)
- Maven: you have settings.xml and profiles (similar interception)
- Ant + Ivy: no interception mechanism — must rewrite config files
- Make/CMake: must modify Makefiles or provide toolchain files
- The build system determines your entire fix strategy

**2. What are ALL the dependency sources?**
- List every repository URL in every build config file
- Check each one with `curl -sI <url>` — which return 200, which return 401/403/404?
- This is the single most important step. One dead repository can block the entire build.

**3. What JDK/compiler/toolchain version is required?**
- Check the project's documentation, CI configs, and compiler settings
- Search for Docker images of that exact toolchain version
- Don't assume alternatives are equivalent — we found behavioral differences between Sun JDK 6 and Zulu OpenJDK 6 that caused test failures

**4. Does the project have internal build infrastructure?**
- Some projects (like Spring) have build support modules that are separate repos or submodules
- Check if those repos/submodules still exist
- If they're dead (SVN externals, deleted repos), find the nearest surviving snapshot

### Phase 2: Dependency Mapping

**1. Extract every dependency declaration**
- For each module, list every dependency with exact coordinates and version
- For Maven/Gradle: `group:artifact:version`
- For Ivy: `org/name/rev`
- For non-standard naming (EBR, OSGi bundles): document the naming scheme

**2. Verify each dependency on public repositories**
- Check Maven Central: `curl -sI https://repo1.maven.org/maven2/<group-path>/<artifact>/<version>/<artifact>-<version>.jar`
- Check other public repos: Gradle Plugin Portal, Red Hat Maven GA, JBoss
- Classify each dependency: AVAILABLE, AVAILABLE_DIFFERENT_COORDS, UNAVAILABLE

**3. For unavailable dependencies, exhaust all options in order**
1. Search for the artifact under different coordinates on Maven Central
2. Search alternative public repositories (Red Hat, JBoss, Eclipse)
3. Search for open-source forks or reimplementations
4. Build from archived source if available
5. Create stub JARs with matching API signatures (last resort)
6. Exclude the dependent module with documented justification (absolute last resort)

**Key lesson learned:** We initially classified JMXMP (15 test failures) and Eclipse JDT compiler as "unavailable proprietary artifacts." Both turned out to be on Maven Central under different coordinates. Always search exhaustively before giving up.

### Phase 3: Build Environment Construction

**1. Containerfile**
- Use the exact JDK version the project was built with, not just a compatible one
- Install the exact compiler if it differs from javac (e.g., Eclipse JDT)
- Set locale and encoding to match the original build environment (affects string formatting and character handling in code and tests)
- Do NOT fake timestamps or system clock — the output should honestly say "built in 2026 from historical source," not pretend to be the original artifact
- Pre-download build tool distributions (Gradle wrapper, Ant) to avoid download failures

**2. Build system configuration**
- For Gradle: init scripts in `~/.gradle/init.d/` (not `-I` flag — doesn't cover buildSrc)
- For Ant+Ivy: replace ivysettings.xml, rewrite ivy.xml files via automated script
- For Maven: settings.xml with mirror and profile overrides
- Never manually edit individual files — always use automated rewriting for reproducibility

**3. Dependency resolution overrides**
- Redirect all dead repositories to working public mirrors
- Set `usepoms=false` (Ivy) or equivalent when transitive resolution pulls dead artifacts
- Add transitive dependencies explicitly when automatic resolution is disabled
- Plugin substitutions: verify not just Maven coordinates but plugin IDs match

**Key lesson learned:** The cn.bestwu propdeps-plugin fork was available on Maven Central with matching coordinates, but it registered under a different Gradle plugin ID. Maven coordinates are necessary but not sufficient — plugin IDs, OSGi bundle names, and other registration mechanisms must also match.

### Phase 4: Iterative Compilation

**1. Start with the fastest feedback loop**
- Compile-only mode first (~2-3 minutes), not full build (~20-40 minutes)
- Fix one failure at a time, rebuild, retry
- Expect 10-25 iterations for a complex project

**2. Fix root causes, not symptoms**
- A single missing dependency can cascade to failures in 20 modules
- Always fix the first failure in the build log, not the last
- Track fixes in a known-fixes database with error signatures and justifications

**3. Track every fix**
- Each fix goes into a structured database (YAML, JSON) with:
  - Error signature (regex pattern matching the failure)
  - Fix type (init-script, substitution, stub-jar, task-exclusion)
  - Justification (why this fix is correct)
  - Version applicability (which versions this fix applies to)

### Phase 5: Test Execution (Do NOT Skip This)

**1. Run the project's own test suite**
- The tests are the primary signal that your build output is correct
- Do not claim the build root works based on compilation alone
- Report actual numbers: total tests, passed, failed, errors, skipped

**2. Classify every failure**
- MISSING_DEP: fixable by adding a dependency (do this)
- JDK_COMPAT: behavioral difference from using a different JDK version/vendor
- INFRA: requires external services (databases, message queues) not in the container
- GENUINE_BUG: real bug in the original code (would fail on the original build too)
- INTENTIONAL: tests designed to fail (validating error handling)

**3. Fix solvable failures iteratively**
- Missing dependencies: search online, add to mapping
- JDK issues: try matching the exact original JDK
- Container limits: raise ulimits (thread/pid limits) for concurrent tests
- Locale/timezone: set container environment variables

**Key lesson learned:** We initially reported "all tests pass" based on compilation success with `-x test`. When we actually ran the tests, we found real issues — missing transitive deps, container thread limits, JMXMP protocol support. Always run the actual tests.

### Phase 6: Equivalence Verification

**1. Download original pre-built artifacts**
- Maven Central, PyPI, npm, crates.io — most package registries keep historical versions forever

**2. Compare at the right abstraction level**
- Don't compare raw bytes (timestamps, compiler metadata will differ)
- Compare class/symbol inventories (are the same files present?)
- Compare public API surfaces (are all methods, fields, types identical?)
- Compare decompiled source for semantic equivalence

**3. Document every difference**
- Expected: compiler metadata, timestamps, manifest headers, debug info
- Unexpected: missing classes, changed method signatures, different types

### Phase 7: Documentation

**1. Document every substitution**
- What was original, what you replaced it with, what the risk is
- Flag the risk level: negligible, low, medium, high

**2. Document design choices**
- Why this JDK version, why this compiler, why this resolver strategy
- What alternatives were considered and rejected, and why

**3. Document limitations**
- What can't be verified (e.g., EBR vs Maven Central JARs when EBR is offline)
- What environments the rebuilt artifacts do/don't work in (e.g., OSGi)

## Anti-Patterns (Things That Wasted Time)

1. **Claiming victory without running tests.** Compilation success != correctness.
2. **Accepting "unavailable" without exhaustive search.** Both JMXMP and Eclipse JDT were available — we just didn't look hard enough initially.
3. **Using a "compatible" JDK instead of the exact one.** Zulu OpenJDK 6 is JCK-certified but caused 6 test failures that Sun JDK 6 didn't.
4. **Using a different compiler without checking if the original is available.** Eclipse JDT 3.3.0 was on Maven Central the entire time.
5. **Committing build artifacts to git.** 1,692 test XML files made the PR unreviewable.
6. **Stubbing when the real artifact exists somewhere.** Always search Maven Central, Red Hat Maven, JBoss, GlassFish repos before creating stubs.
7. **Manual file editing instead of automated rewriting.** 22 ivy.xml files need a script, not hand-editing.

## What Generalizes Beyond Java

The methodology applies to any language with package registries:

| Phase | Java | Python | C/C++ | Rust | Go |
|-------|------|--------|-------|------|-----|
| Dependency audit | Maven Central, Gradle Plugin Portal | PyPI | system packages, vcpkg, conan | crates.io | Go module proxy |
| Container base | JDK Docker images | Python Docker images | GCC/Clang Docker images | Rust Docker images | Go Docker images |
| Build interception | Gradle init scripts, Maven settings.xml | pip.conf, pyproject.toml overrides | CMake toolchain files, environment vars | .cargo/config.toml | GOPROXY, go.mod replace |
| Equivalence test | javap API comparison | AST comparison, import graphs | nm symbol tables, abidiff | cargo symbol comparison | go version -m build info |
| Original artifacts | Maven Central (keeps everything) | PyPI (keeps everything) | Distro archives (varies) | crates.io (keeps everything) | Go module proxy (keeps everything) |
