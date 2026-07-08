# FeatureBench Workflow

4-node pipeline for implementing new features in Python codebases with explicit interface specifications.

## Graph

```
study (FnNode) → builder (AgentNode) → gate_verify (GateNode) → auto_merge (FnNode)
                      ↑                        │
                      └── RELOOP (max 3) ──────┘
```

- **study**: Scans repo structure, package layout, placeholder implementations, and reads `/tmp/task-instruction.md`
- **builder**: Implements the feature following exact interface specs (function signatures, import paths, types), runs tests
- **gate_verify**: Checks builder committed changes and reports test status
- **auto_merge**: Fast-forwards the base branch to include the implementation

## Usage

```bash
factory workflow run featurebench --project /path/to/repo
```

Typically invoked inside a Harbor container where the task instruction contains detailed interface definitions.
