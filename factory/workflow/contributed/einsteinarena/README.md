# Einstein Arena Workflow

Einstein Arena RL training workflow (MVP) - trains agents on Einstein Arena mathematical optimization tasks.

## Graph

```
study (FnNode) → train (AgentNode)
```

- **study**: Creates `.factory/rl/` directory for training artifacts
- **train**: Runs RL training loop with mock rollouts (real vLLM integration deferred to v2)

## Usage

```bash
factory ceo /path/to/project --mode einsteinarena
```

## Notes

This is an MVP implementation that uses mock rollout generation. Real vLLM HTTP backend integration is tracked in the project backlog.
