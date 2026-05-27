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

The factory is a three-layer system:

1. **Python CLI** (`factory/`): Pure tools that don't make decisions. Entry point is `factory/cli.py`.
2. **CEO Agent** (`factory/agents/prompts/ceo.md`): Orchestrates the full workflow. Spawned via `factory ceo /path`.
3. **Specialist Agents** (`factory/agents/`): Eight subprocesses spawned by the CEO via `factory agent <role>`.

Agent roles: Researcher, Strategist, Builder, Reviewer, Evaluator, Archivist, Distiller, Refiner, CEO.

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
