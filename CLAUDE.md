# factory-tracing

Distributed tracing for the re:factory multi-agent software factory using OpenTelemetry.

## Package Structure

```
src/factory_tracing/
  __init__.py        — public API: setup_tracing(), get_tracer(), shutdown_tracing()
  config.py          — TracingConfig dataclass, reads from env vars
  provider.py        — TracerProvider singleton, OTLP exporter setup
  spans.py           — agent invocation span helpers (Phase 2)
  propagation.py     — TRACEPARENT injection for subprocesses (Phase 3)
  py.typed           — PEP 561 marker
```

## Running Tests

```bash
pip install -e '.[dev]'
pytest -v
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FACTORY_TRACING_ENABLED` | No | `false` | Set to `1`/`true` to enable tracing |
| `LANGFUSE_HOST` | When enabled | — | Langfuse instance URL (e.g. `http://localhost:3000`) |
| `LANGFUSE_PUBLIC_KEY` | When enabled | — | Langfuse public API key |
| `LANGFUSE_SECRET_KEY` | When enabled | — | Langfuse secret API key |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | Derived from LANGFUSE_HOST | Override OTLP endpoint |
| `OTEL_SERVICE_NAME` | No | `factory-orchestrator` | OTel service name |

Copy `.env.example` to `.env` and fill in your Langfuse credentials.

## Dependency Boundary

- **Production** (core deps): `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
- **Dev only** (`[dev]` extras): `pytest`, `pytest-asyncio`, `langfuse`, `python-dotenv`

The `factory_tracing` package must NEVER import `langfuse` or `dotenv`. Those are dev/test dependencies only.
