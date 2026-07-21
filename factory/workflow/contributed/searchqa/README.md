# SearchQA Workflow

Single-node question-answering pipeline for the SearchQA benchmark (Harbor).

## Graph

```
builder (AgentNode)
```

- **builder**: Reads `/tmp/task-instruction.md` (question + search results), outputs an answer in `<answer>` tags

## Usage

```bash
factory workflow run searchqa --project /path/to/repo
```

Typically invoked inside a Harbor container where the task instruction is pre-populated at `/tmp/task-instruction.md`.
