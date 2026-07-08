# TerminalBench Workflow

4-node pipeline for real-world engineering tasks in terminal environments — from compiling legacy software to scientific computing to system configuration.

## Graph

```
study (FnNode) → builder (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                      ↑                        │
                      └── RELOOP (max 3) ──────┘
```

- **study**: Inventories workspace, git state, available languages/compilers/tools, and reads `/tmp/task-instruction.md`
- **builder**: Solves the engineering task — installs dependencies, writes code, verifies result, commits
- **gate_verify**: Checks builder committed changes and scans for success/failure signals
- **auto_merge**: Fast-forwards the base branch to include the solution

## Usage

```bash
factory workflow run terminalbench --project /path/to/repo
```

Typically invoked inside a Harbor container. Tasks span software engineering, scientific computing, system administration, security, ML, data processing, and more.
