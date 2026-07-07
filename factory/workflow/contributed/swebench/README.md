# SWE-bench Workflow

Minimal 4-node pipeline for solving GitHub issues in containerized evaluation (Harbor).

## Graph

```
study (FnNode) → builder (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                      ↑                        │
                      └── RELOOP (max 3) ──────┘
```

- **study**: Scans repo structure, test files, and reads `/tmp/task-instruction.md`
- **builder**: Implements the minimal bug fix, runs tests, commits
- **gate_verify**: Checks builder committed changes and reports test status
- **auto_merge**: Fast-forwards the base branch to include the fix

## Usage

```bash
factory workflow run swebench --project /path/to/repo
```

Typically invoked inside a Harbor container where the task instruction is pre-populated at `/tmp/task-instruction.md`.
