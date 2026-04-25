# Setup Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | System or [pyenv](https://github.com/pyenv/pyenv) |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Latest | `npm install -g @anthropic-ai/claude-code` |
| Node.js | 18+ | Required for Claude Code and MCP servers |
| [uv](https://docs.astral.sh/uv/) | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (for dev install) |
| tmux | Any | `brew install tmux` (optional, for long-running sessions) |

**Claude Code must be installed and authenticated.** The Factory spawns `claude` as subprocesses — it does not call the Claude API directly. However you've authenticated Claude Code (API key, Vertex AI, etc.) is how the Factory will access Claude.

## Installation

### Option A: From source (recommended)

The factory evolves fast — installing from source lets you `git pull` to stay current.

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .
```

### Option B: From pip

```bash
pip install git+https://github.com/akashgit/remote-factory.git
```

### Option C: One-liner

```bash
curl -sSf https://raw.githubusercontent.com/akashgit/remote-factory/main/install.sh | bash
```

### Verify

```bash
factory --help
```

If you installed from source without `uv tool install`, prefix all commands with `uv run python -m factory` instead of `factory`.

## CEO Agent Registration

Register the Factory CEO as a Claude Code agent so you can launch it from anywhere:

```bash
factory install
```

This writes `~/.claude/agents/factory-ceo.md`. Now you can:

```bash
# From any terminal
factory ceo ~/my-project

# From within any Claude Code session
claude --agent factory-ceo
```

Re-run `factory install` after updating the factory to pick up prompt changes.

## MCP Servers

The factory uses [MCP](https://modelcontextprotocol.io/) for extended capabilities. Configuration lives in `.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

**Playwright MCP** enables browser automation for UI testing. Claude Code auto-discovers `.mcp.json` — no manual setup needed.

To add MCP servers to a target project, create a `.mcp.json` in its root. The Builder agent will use available MCP tools when working on that project.

## Optional: Obsidian Vault

The factory can archive experiment history and cross-project knowledge to an Obsidian vault:

```bash
factory vault-init
```

This creates a vault directory with the expected structure. Configure a custom path:

```bash
export FACTORY_VAULT_PATH=~/my-factory-vault
```

If unset and the default path doesn't exist, vault features are skipped gracefully.

## Full Setup From Scratch

```bash
# 1. Install tooling
npm install -g @anthropic-ai/claude-code     # Claude Code
curl -LsSf https://astral.sh/uv/install.sh | sh  # uv (optional, for dev install)

# 2. Authenticate Claude Code (if not already done)
claude  # follow the prompts

# 3. Install the factory
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory && uv sync && uv tool install -e .

# 4. Register CEO agent
factory install

# 5. Optional: Initialize vault
factory vault-init

# 6. Verify
factory --help
factory detect /path/to/any/project
```
