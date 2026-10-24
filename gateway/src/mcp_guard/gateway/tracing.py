"""Tracing with OpenTelemetry, off unless configured.

OTEL_EXPORTER_OTLP_ENDPOINT turns it on: this process's spans are then sent over OTLP to
that address, with OTEL_EXPORTER_OTLP_HEADERS as request headers (the backend's key). Any
OpenTelemetry backend works; ours is Langfuse Cloud (see .env.example). Without it, spans
go nowhere, which is how the tests run.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def start_tracing(service_name: str) -> None:
    """Send spans to the configured backend, if there is one, until the process exits."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    # The provider sends any spans still waiting at a normal exit; stop_tracing covers the rest.
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def stop_tracing() -> None:
    """Send the spans still waiting, for a process whose exit hooks may not run."""
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
