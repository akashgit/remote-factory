# LegacyBench Workflow

4-node pipeline for fixing bugs in legacy code — COBOL, Fortran, C, Java 7, Assembly.

## Graph

```
study (FnNode) → builder (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                      ↑                        │
                      └── RELOOP (max 3) ──────┘
```

- **study**: Scans workspace for legacy source files, build system (Makefile), and reads `/tmp/task-instruction.md`
- **builder**: Fixes the described bug while preserving original language standard and coding patterns
- **gate_verify**: Checks builder committed changes and scans for success/failure signals
- **auto_merge**: Fast-forwards the base branch to include the fix

## Usage

```bash
factory workflow run legacybench --project /path/to/repo
```

Typically invoked inside a Harbor container. The benchmark uses hidden test inputs — solutions must implement general algorithms, not hardcode outputs.
