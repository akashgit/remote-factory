# Build-Root Mode Prerequisites

Build-root mode produces verified build environments for historical Java projects. It runs a 4-stage gated pipeline (DEP RESOLVE → ARTIFACT RECOVERY → COMPILE → TEST) inside containers.

## System Requirements

- **OS:** Linux (primary), macOS (Podman support with caveats)
- **CPU:** 2+ cores
- **RAM:** 4GB+ for container builds
- **Disk:** 10GB+ free (container images, Gradle caches, local Maven repo)

## Required Tools

| Tool | Minimum Version | Install |
|------|----------------|---------|
| Podman | 4.0+ | `sudo dnf install podman` (Fedora/RHEL) or `sudo apt install podman` (Debian/Ubuntu) |
| Python | 3.11+ | `sudo dnf install python3` or `sudo apt install python3` |
| git | 2.25+ | `sudo dnf install git` or `sudo apt install git` |

## Optional Tools

- **Docker** — Use as an alternative container runtime via `CONTAINER_RUNTIME=docker`
- **JDK** — For local debugging outside containers (match the target project's JDK version)

## Python Dependencies

```bash
pip install -e .    # or: uv sync
```

PyYAML is required for known-fixes database parsing.

## Verification

Run the prerequisites check script:

```bash
./scripts/check-prerequisites.sh
```

This prints a pass/fail checklist for each required tool.

## Quick Start

1. Configure `factory.md` with a `## Build Root` section:

   ```markdown
   ## Build Root
   - project_repo: https://github.com/spring-projects/spring-framework
   - version_tag: v5.2.9
   - jdk_version: 11
   ```

2. Run build-root mode:

   ```bash
   factory ceo /path/to/project --mode build-root
   ```

3. The pipeline will:
   - Clone the target repo at the specified version tag
   - Build a container with the correct JDK
   - Resolve dependencies (Stage 1)
   - Recover missing artifacts (Stage 2)
   - Compile all modules (Stage 3)
   - Run tests (Stage 4)
   - Commit every fix attempt, revert failures

Results are written to `results/build-root-status.json`.
