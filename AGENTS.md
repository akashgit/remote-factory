<!-- Keep in sync with CLAUDE.md -->
# AGENTS.md

This file provides guidance to Codex CLI and other AGENTS.md-compatible tools when working with this repository.

## Build & Run

```bash
uv sync                          # Install all deps (including dev group)
factory --help                   # Verify CLI entry point
```

## Test

```bash
pytest -v                        # Full suite
pytest tests/test_models.py -v   # Single file
pytest -k "test_detect" -v       # By name pattern
pytest --cov                     # With coverage
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Lint & Type Check

```bash
ruff check .                     # Lint
ruff check --fix .               # Lint with autofix
mypy factory/                    # Type check
```

## Style

- Python 3.11+, use `X | Y` unions, not `Union[X, Y]`
- Snake_case everywhere
- 100 char line length (enforced by ruff)
- Pydantic models use `ConfigDict(strict=True, extra="forbid")`
- Async/await by default
- Structured logging via `structlog`

## Architecture

The factory is a four-layer system:

1. **Python CLI** (`factory/cli/`): Pure tools that don't make decisions. Entry point is `factory/cli/_main.py`.
2. **LangGraph Runtime** (`factory/workflow/`): Compiles the Factory DSL, persists SQLite checkpoints, and owns routing, fanout/fanin, loops, interrupts, and resume.
3. **CEO Agent** (`factory/agents/prompts/ceo.md`): Interactive client and gate evaluator for the graph. Spawned via `factory ceo /path`.
4. **Specialist Agents** (`factory/agents/`): Eight subprocesses invoked by workflow nodes via `factory agent <role>`.

Agent roles: Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, Distiller, Failure Analyst, CEO.

Key modules: `factory/workflow/langgraph.py` (DSL compiler and state), `factory/workflow/executor.py` (domain operations and persistence), and `factory/adversarial.py` (GAN-style adversarial evaluation).

## MCP Server

The factory exposes tools via MCP (Model Context Protocol):

```bash
factory serve-mcp
```

Or configure in `~/.codex/config.toml`:

```toml
[mcp_servers.factory]
command = "factory"
args = ["serve-mcp"]
```

## Installing Agents

```bash
factory install --runner codex    # Install TOML agents to ~/.codex/agents/
factory install --runner claude   # Install Markdown agents to ~/.claude/agents/
```
