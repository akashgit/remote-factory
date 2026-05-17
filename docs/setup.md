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

If running from source without `uv tool install`, prefix commands with `uv run` (e.g., `uv run factory ceo "..."`). If you've installed the CLI, bare `factory` works directly.

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

## What You Don't Need

- **No Obsidian.** The factory stores all state locally in `.factory/` inside each project and `~/.factory/` globally. Earlier versions used an Obsidian vault — that dependency has been removed entirely.
- **No external database.** Everything is flat files: TSV for experiment history, JSON for config and reports, markdown for strategy and archive notes.
- **No API keys for the factory itself.** The factory spawns Claude Code subprocesses — however you've authenticated Claude Code is how the factory accesses Claude. No separate Anthropic API key is needed.

## Environment Variables

The factory reads these environment variables. None are required for basic usage — the defaults work out of the box.

> **Tip:** All `FACTORY_*` variables below can also be set in `~/.factory/config.toml`, which supports credential profiles and secret masking. See the [Configuration Reference](configuration.md#user-configuration-factoryconfigtoml) for details. Env vars always take precedence over config.toml values.

### Claude Code Authentication

The factory inherits Claude Code's authentication. Configure whichever method you use:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Direct API authentication |
| `CLAUDE_CODE_USE_VERTEX` | Set to `1` for Google Cloud Vertex AI |
| `ANTHROPIC_VERTEX_PROJECT_ID` | Vertex AI project ID |
| `CLOUD_ML_REGION` | Vertex AI region (e.g., `us-east5`) |

### Factory Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `FACTORY_PROJECTS_DIR` | Parent directory for projects created from prompts | `~/factory-projects` |
| `FACTORY_MODEL` | Model override for agent subprocesses | *(Claude Code default)* |
| `FACTORY_PLAYBOOKS_DIR` | Directory for ACE-evolved agent playbooks | `~/.factory/playbooks` |
| `FACTORY_REGISTRY_DIR` | Override global registry location | `~/.factory` |
| `FACTORY_VAULT_PATH` | Legacy: path to Obsidian vault (optional, for Archivist) | *(unset — not needed)* |
| `FACTORY_RUNNER` | CLI backend: `claude` or `bob` | `claude` |
| `FACTORY_RUNNER_QUIET` | Suppress runner output (`1` to enable) | *(unset)* |

### Bob Shell (alternative runner)

| Variable | Purpose | Default |
|----------|---------|---------|
| `BOBSHELL_API_KEY` | Bob Shell API key | *(required if using Bob)* |
| `FACTORY_BOB_DRY_RUN` | Test mode — no API calls (`1` to enable) | *(unset)* |
| `FACTORY_BOB_MAX_INVOCATIONS_PER_CYCLE` | Per-cycle invocation ceiling | `8` |

### Notifications (optional)

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for push notifications |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for notifications |

### CEO Behavior (advanced)

| Variable | Purpose | Default |
|----------|---------|---------|
| `FACTORY_CEO_RESPAWN_DISABLED` | Disable automatic CEO respawn on crash (`1`) | *(unset)* |
| `FACTORY_CEO_MAX_RESPAWNS` | Maximum respawn attempts per cycle | `3` |

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

# 5. (Optional) Set up config file with credential profiles
factory config edit                          # Creates ~/.factory/config.toml

# 6. Verify
factory --help
factory detect /path/to/any/project
```
