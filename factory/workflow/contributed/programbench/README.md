# ProgramBench Workflow

Discovery-first reverse engineering pipeline for reproducing compiled binary behavior.

## Graph

```
discover (AgentNode) → plan (FnNode) → builder (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                                             ↑                        │
                                             └── RELOOP (max 3) ──────┘
```

- **discover**: Probes the compiled binary at `/workspace/executable` exhaustively — flags, stdin, exit codes
- **plan**: Checkpoint node confirming discovery is complete
- **builder**: Writes C source reproducing all discovered behaviors, creates `compile.sh`, runs differential testing
- **gate_verify**: Verifies `compile.sh` exists and compiles successfully
- **auto_merge**: Fast-forwards the base branch to include the solution

## Usage

```bash
factory workflow run programbench --project /path/to/repo
```

Typically invoked inside a Harbor container with a compiled binary at `/workspace/executable`.
