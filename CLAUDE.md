# factory-tracing

Distributed tracing for the re:factory multi-agent software factory using OpenTelemetry.

## Package Structure

```
src/factory_tracing/
  __init__.py        — public API: setup_tracing(), get_tracer(), shutdown_tracing(), TracingIntegration
  config.py          — TracingConfig dataclass, reads from env vars
  provider.py        — TracerProvider singleton, OTLP exporter setup
  spans.py           — agent invocation span helpers (Phase 2)
  propagation.py     — TRACEPARENT injection for subprocesses (Phase 3)
  integration.py     — TracingIntegration high-level API (Phase 4+5)
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

## Span Attribute Reference

### Cycle span (`factory.cycle`)

| Attribute | Type | Set by | Description |
|---|---|---|---|
| `factory.run.id` | string | `start_cycle()` | Unique cycle/run identifier |
| `factory.project.name` | string | `start_cycle()` | Project name |
| `factory.mode` | string | `start_cycle()` | Execution mode (e.g. `improve`) |
| `factory.experiment.id` | string | `start_cycle(experiment_id=)` | Experiment identifier (optional) |
| `factory.hypothesis.id` | string | `start_cycle(hypothesis_id=)` | Hypothesis identifier (optional) |
| `factory.hypothesis.category` | string | `start_cycle(hypothesis_category=)` | FEEC category: FIX/EXPLOIT/EXPLORE/COMBINE (optional) |
| `factory.experiment.verdict` | string | `record_experiment_verdict()` | Experiment outcome: KEEP/REVERT |
| `factory.experiment.composite_score` | float | `record_experiment_verdict()` | Composite eval score |
| `langfuse.observation.type` | string | `trace_factory_cycle()` | Always `"span"` |
| `langfuse.session.id` | string | `start_cycle()` | `experiment_id` if provided, else `run_id` |
| `langfuse.trace.metadata.experiment_id` | string | `start_cycle(experiment_id=)` | Filterable in Langfuse UI (optional) |
| `langfuse.trace.metadata.hypothesis_category` | string | `start_cycle(hypothesis_category=)` | Filterable in Langfuse UI (optional) |

### Cycle span events

| Event name | Attributes | Set by | Description |
|---|---|---|---|
| `eval.result` | `eval.<dimension>: float` | `record_eval_result()` | Eval dimension scores (e.g. `eval.tests`, `eval.lint`) |

### Agent span (`invoke_agent {role}`)

| Attribute | Type | Set by | Description |
|---|---|---|---|
| `gen_ai.operation.name` | string | `trace_agent_invocation()` | Always `"invoke_agent"` |
| `gen_ai.agent.name` | string | `trace_agent_invocation()` | Agent role (e.g. `researcher`) |
| `gen_ai.system` | string | `trace_agent_invocation()` | Always `"anthropic"` |
| `gen_ai.usage.input_tokens` | int | `record_agent_result()` | Input token count (optional) |
| `gen_ai.usage.output_tokens` | int | `record_agent_result()` | Output token count (optional) |
| `gen_ai.usage.cost` | float | `record_agent_result()` | Cost in USD (optional) |
| `factory.run.id` | string | `trace_agent_invocation()` | Cycle/run identifier |
| `factory.project.name` | string | `trace_agent_invocation()` | Project name |
| `factory.task.summary` | string | `trace_agent_invocation()` | Agent task description |
| `subprocess.returncode` | int | `record_agent_result()` | Agent process exit code |
| `subprocess.duration_ms` | float | `record_agent_result()` | Agent execution duration |
| `langfuse.observation.type` | string | `trace_agent_invocation()` | Always `"span"` |
| `langfuse.session.id` | string | `trace_agent_invocation()` | Cycle/run identifier |
| `langfuse.trace.tags` | tuple | `trace_agent_invocation()` | Agent role for Langfuse filtering |
