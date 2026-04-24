# Setup Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | System or [pyenv](https://github.com/pyenv/pyenv) |
| [uv](https://docs.astral.sh/uv/) | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Latest | `npm install -g @anthropic-ai/claude-code` |
| Node.js | 18+ | Required for Claude Code and MCP servers |
| tmux | Any | `brew install tmux` (optional, for long-running sessions) |

## Installation

```bash
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync

# Install the `factory` command globally
uv tool install -e .

# Register the CEO as a Claude Code agent
factory install
```

Verify:

```bash
factory --help
```

If you skip `uv tool install`, prefix all commands with `uv run python -m factory` instead of `factory`.

### One-liner install

```bash
curl -sSf https://raw.githubusercontent.com/akashgit/remote-factory/main/install.sh | bash
```

## Claude API Access

The factory needs access to Claude. Two options:

### Option A: Anthropic API (simplest)

```bash
export ANTHROPIC_API_KEY=<your-key>
```

### Option B: Google Vertex AI

```bash
# Authenticate
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>

# Add to ~/.zshrc or ~/.bashrc
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=us-east5
export ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>
```

## CEO Agent Registration

The Factory CEO runs as a Claude Code agent. Register it once:

```bash
factory install
```

This writes `~/.claude/agents/factory-ceo.md`. Now you can launch the CEO from anywhere:

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

This creates `~/obsidian-vaults/factory/` with the expected directory structure. Configure a custom path:

```bash
export FACTORY_VAULT_PATH=~/my-factory-vault
```

If unset and the default path doesn't exist, vault features are skipped gracefully.

## Optional: Telegram Notifications

```bash
export TELEGRAM_BOT_TOKEN=<token>
export TELEGRAM_CHAT_ID=<chat-id>
```

## Full Setup From Scratch

```bash
# 1. Install tooling
curl -LsSf https://astral.sh/uv/install.sh | sh
npm install -g @anthropic-ai/claude-code

# 2. Clone and install
git clone https://github.com/akashgit/remote-factory.git
cd remote-factory
uv sync
uv tool install -e .

# 3. Set API key (pick one)
export ANTHROPIC_API_KEY=<your-key>
# OR set up Vertex AI (see above)

# 4. Register CEO agent
factory install

# 5. Optional: Initialize vault
factory vault-init

# 6. Verify
factory --help
factory detect /path/to/any/project
```
