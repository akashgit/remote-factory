# LangFuse Local Development

LangFuse provides LLM observability and tracing for the Red Hat Agents system.

## Quick Start (Zero Configuration)

**Tracing works automatically with no configuration.** Just start LangFuse:

```bash
# From project root
scripts/langfuse start
```

That's it! The agents system automatically sends traces to LangFuse when it's running.

### How It Works

The tracing module auto-detects LangFuse at `localhost:3000` using pre-configured development credentials that match the docker-compose setup. No environment variables, no `.env` files, no manual configuration needed.

| Component | Default | Notes |
|-----------|---------|-------|
| Host | `http://localhost:3000` | Auto-detected |
| Public Key | `pk-lf-dev-local-key` | Matches docker-compose |
| Secret Key | `sk-lf-dev-local-key` | Matches docker-compose |

### Viewing Traces

1. Start LangFuse: `scripts/langfuse start`
2. Run a query: `./analyze "What is Red Hat's Q2 revenue?"`
3. Open browser: `scripts/langfuse open --traces`
4. Login: `dev@localhost.local` / `devpassword123`

Use `--trace-id` to tag queries for easy searching:
```bash
./analyze --trace-id my-test "What is Red Hat's Q2 revenue?"
# Search by "Session ID" in LangFuse UI to find it
```

## CLI Commands

All commands run from the **project root** directory:

```bash
scripts/langfuse <command> [options]

Commands:
  start       Start LangFuse services
  stop        Stop LangFuse services
  status      Check service health and trace count
  logs        View service logs
  open        Open LangFuse UI in browser
  reset       Delete all traces
  config      Show configuration
  setup-llm   Set up LLM for evaluations (optional)
```

Common examples:
```bash
scripts/langfuse start              # Start services
scripts/langfuse status             # Check health
scripts/langfuse stop               # Stop (preserve data)
scripts/langfuse stop --volumes     # Stop and delete all data
scripts/langfuse logs -f            # Stream all logs
scripts/langfuse open               # Open browser
```

## Requirements

- **Podman** (recommended) or **Docker**
  - macOS: `brew install podman`
  - Fedora: `dnf install podman`
- **podman-compose** (if using Podman)
  - `pip install podman-compose`

## Disabling Tracing

To disable tracing without stopping LangFuse:
```bash
export LANGFUSE_TRACING_ENABLED=false
```

---

## LLM Connection Setup (Optional)

> **This section is OPTIONAL.** Tracing works without any LLM connection.
> LLM connections are only needed for LangFuse's evaluation and playground features.

LangFuse can use LLM models to power its evaluation and playground features. This requires a separate API key stored in your shell profile (not the project).

### Credential Storage

**Store credentials in `~/.zshrc` (or `~/.bashrc`), not in `.env` files:**

```bash
# Add to ~/.zshrc
export GOOGLE_API_KEY=your-google-ai-studio-key
```

After adding, run `source ~/.zshrc` or open a new terminal.

### Google AI Studio (Recommended)

Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey):

```bash
# 1. Add to ~/.zshrc
export GOOGLE_API_KEY=your-api-key

# 2. Configure LangFuse
scripts/langfuse setup-llm --adapter google-ai-studio
```

Available models: `gemini-3.1-pro-preview`, `gemini-3-flash-preview`

### Other Providers

```bash
# OpenAI
export OPENAI_API_KEY=sk-xxx
scripts/langfuse setup-llm --adapter openai

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-xxx
scripts/langfuse setup-llm --adapter anthropic
```

### Managing LLM Connections

```bash
scripts/langfuse setup-llm --list     # List connections
scripts/langfuse setup-llm --delete   # Delete all
scripts/langfuse setup-llm --force    # Update existing
```

### Setting Default Model

After creating a connection, set the default in the UI:
1. `scripts/langfuse open`
2. **Project Settings** > **Evaluators** > **+ Set up Evaluator**
3. Select model (e.g., `gemini-3.1-pro-preview`)

---

## Architecture

LangFuse v3 runs these services:
- **langfuse-web** (port 3000) - Web UI and API
- **langfuse-worker** (port 3030) - Background processing
- **postgres** (port 5432) - Main database
- **clickhouse** (port 8123, 9000) - Analytics database
- **redis** (port 6379) - Queue and cache
- **minio** (port 9090) - Object storage

## Troubleshooting

### Podman machine not starting (macOS)
```bash
podman machine stop
podman machine rm
podman machine init --cpus 4 --memory 8192
podman machine start
```

### Containers failing to start
```bash
scripts/langfuse logs --service web
scripts/langfuse logs --service worker
```

### Reset everything
```bash
scripts/langfuse stop --volumes
scripts/langfuse start
```
