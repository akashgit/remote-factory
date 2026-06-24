# Langfuse Local Development

Langfuse provides LLM observability and tracing for the factory system.

## Quick Start

1. Start Langfuse services:
```bash
cd infra/langfuse && docker compose up -d
```

2. Run the factory with Langfuse tracing enabled:

```bash
export LANGFUSE_HOST=http://localhost:3000
export LANGFUSE_BASE_URL=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-lf-dev-local-key
export LANGFUSE_SECRET_KEY=sk-lf-dev-local-key
export TELEMETRY_PLATFORM=langfuse

factory ceo /path/to/project
```

3. Open `http://localhost:3000` to view traces. Login: `dev@localhost.local` / `devpassword123`

Add these to your `~/.bashrc` or `~/.zshrc` to persist across sessions.

The factory creates a single Langfuse trace per CEO cycle. The trace structure:

```
Trace: factory:<project>/<mode>
└── Root span (cycle session)
    ├── agent:ceo          ← interactive CEO session (streamed in real-time)
    │   ├── tool:Bash
    │   ├── assistant_message
    │   └── ...
    ├── agent:researcher   ← headless specialist (transcript ingested on completion)
    ├── agent:strategist
    ├── agent:builder
    └── agent:qa
```

- **CEO session** is traced incrementally via a background thread that tails the Claude Code transcript JSONL every 5 seconds. The span exists from session start, so partial data is visible even if the session is killed.
- **Specialist agents** have their transcripts batch-ingested when the agent completes.
- The trace name (`factory:<project>/<mode>`) is reasserted via the ingestion API to prevent the SDK from overwriting it with child observation names.

### Environment Variables

| Variable | Default | Notes |
|-----------|---------|-------|
| `LANGFUSE_HOST` | — | Required. Set to `http://localhost:3000` for local dev |
| `LANGFUSE_BASE_URL` | — | Same as HOST (some SDK versions use this) |
| `LANGFUSE_PUBLIC_KEY` | — | `pk-lf-dev-local-key` for local dev |
| `LANGFUSE_SECRET_KEY` | — | `sk-lf-dev-local-key` for local dev |
| `TELEMETRY_PLATFORM` | — | Set to `langfuse` to enable |

### Verifying Traces

```bash
python scripts/verify_langfuse_trace.py <project-name> [--after TIMESTAMP]
```

This checks: single trace exists, correct name format, root span, agent spans nested under root, CEO span present, transcript observations ingested.

## CLI Commands

All commands run from the **project root** directory:

```bash
scripts/langfuse-setup start    # Start LangFuse services
scripts/langfuse-setup stop     # Stop services
scripts/langfuse-setup status   # Show status and credentials
```

## Requirements

- **Docker** or **Podman** — any of `docker compose`, `docker-compose`, or `podman-compose` works

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
scripts/langfuse-setup setup-llm --adapter google-ai-studio
```

Available models: `gemini-3.1-pro-preview`, `gemini-3-flash-preview`

### Other Providers

```bash
# OpenAI
export OPENAI_API_KEY=sk-xxx
scripts/langfuse-setup setup-llm --adapter openai

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-xxx
scripts/langfuse-setup setup-llm --adapter anthropic
```

### Managing LLM Connections

```bash
scripts/langfuse-setup setup-llm --list     # List connections
scripts/langfuse-setup setup-llm --delete   # Delete all
scripts/langfuse-setup setup-llm --force    # Update existing
```

### Setting Default Model

After creating a connection, set the default in the UI:
1. `scripts/langfuse-setup status`
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
cd infra/langfuse && docker compose logs web
cd infra/langfuse && docker compose logs worker
```

### Reset everything
```bash
scripts/langfuse-setup stop
cd infra/langfuse && docker compose down --volumes
scripts/langfuse-setup start
```
