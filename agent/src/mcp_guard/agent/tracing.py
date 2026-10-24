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
    # The provider sends any spans still waiting when the process exits.
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
