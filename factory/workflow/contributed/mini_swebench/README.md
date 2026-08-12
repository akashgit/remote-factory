# mini-swebench

Bash-only SWE-bench solver using direct LLM API calls (LLMNode), replicating mini-SWE-agent's architecture.

## Graph

```
read_task → solver (LLMNode) → gate_verify → auto_merge
                ↑                    │
                └──── RELOOP ────────┘
```

## Usage

```bash
factory workflow run mini-swebench /path/to/project
```

## Nodes

- **read_task** (FnNode) — reads `/tmp/task-instruction.md`
- **solver** (LLMNode) — direct Anthropic API with bash-only tool, no Claude Code
- **gate_verify** (GateNode) — checks commits exist and tests pass
- **auto_merge** (FnNode) — merges changes to default branch
