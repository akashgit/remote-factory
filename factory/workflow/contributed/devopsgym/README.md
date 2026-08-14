# DevOps Gym Workflow

4-node pipeline for solving build/configuration tasks — Maven, Gradle, Go modules, Make, Docker, CI/CD.

## Graph

```
study (FnNode) → solver (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                      ↑                        │
                      └── RELOOP (max 3) ──────┘
```

- **study**: Scans workspace for build files (pom.xml, build.gradle, go.mod, Makefile, Dockerfile, CI/CD configs) and reads `/tmp/task-instruction.md`
- **solver**: Fixes the described build/configuration issue while preserving the existing build system
- **gate_verify**: Checks solver committed changes and attempts to build with the detected build system
- **auto_merge**: Fast-forwards the base branch to include the fix

## Usage

```bash
factory workflow run devopsgym /path/to/repo
```

Typically invoked inside a Harbor container. The benchmark uses hidden verification steps — solutions must implement general fixes, not hardcode outputs.
