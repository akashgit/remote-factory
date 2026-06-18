import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import factory_tracing.provider as _provider_mod


@pytest.fixture
def test_provider():
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "test-service"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    old = _provider_mod._provider
    _provider_mod._provider = provider

    yield provider, exporter

    _provider_mod._provider = old
    exporter.clear()
    provider.shutdown()
