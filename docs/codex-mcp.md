# Codex CLI: MCP Server Setup

The Factory exposes its tools via the Model Context Protocol (MCP). This allows Codex CLI to call factory commands directly as tool invocations.

## Quick Start

```bash
codex mcp add factory -- factory serve-mcp
```

This registers the factory MCP server with Codex CLI. The server runs over stdio and exposes all factory subcommands as MCP tools.

## Manual Configuration

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.factory]
command = "factory"
args = ["serve-mcp"]
```

## Prerequisites

The `factory` CLI must be installed and on PATH:

```bash
uv tool install remote-factory
# or from source
uv tool install git+https://github.com/akashgit/remote-factory
```

Verify with:

```bash
factory --help
factory serve-mcp  # should start and wait for MCP messages on stdin
```

## Available Tools

The MCP server exposes these factory operations:

| Tool | Description |
|------|-------------|
| `detect` | Detect project state |
| `discover` | Introspect project and generate eval profile |
| `eval` | Run project evaluations |
| `begin` | Start a new experiment |
| `finalize` | Finalize an experiment with a verdict |
| `history` | Show experiment history |
| `status` | Print project status summary |
| `study` | Analyze codebase and write observations |
| `backlog-list` | List pending backlog items |
| `backlog-add` | Add a backlog item |
| `backlog-remove` | Remove a backlog item |

## Installing Codex Agents

To install factory specialist agents for direct invocation:

```bash
factory install --runner codex
```

This writes TOML agent files to `~/.codex/agents/factory-*.toml`. Use them with:

```bash
codex --agent factory-researcher
codex --agent factory-builder
codex --agent factory-ceo
```

## Troubleshooting

**MCP server not found:** Ensure `factory` is on your PATH. Run `which factory` to verify.

**Connection refused:** The MCP server uses stdio transport, not HTTP. It reads from stdin and writes to stdout. Codex CLI handles the connection automatically when configured via `codex mcp add` or `config.toml`.
