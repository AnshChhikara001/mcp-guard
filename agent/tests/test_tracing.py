"""One chat turn is one trace across the Agent, the model call, the Gateway and the downstream call.

The terminal chat and the Gateway run as their own processes, the way they do for real, and
send their spans to a fake OpenTelemetry backend that records them.
"""

import os
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue

from conftest import start_gateway


@dataclass(frozen=True)
class RecordedSpan:
    service: str
    name: str
    trace_id: bytes
    span_id: bytes
    parent_span_id: bytes
    start_ns: int
    end_ns: int
    attributes: dict[str, object]


@dataclass
class FakeBackend:
    """A local OTLP/HTTP endpoint that records every span sent to it."""

    url: str
    paths: set[str] = field(default_factory=set[str])
    spans: list[RecordedSpan] = field(default_factory=list[RecordedSpan])


@pytest.fixture
def backend() -> Iterator[FakeBackend]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    recorder = FakeBackend(url=f"http://127.0.0.1:{server.server_port}")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = ExportTraceServiceRequest()
            request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            recorder.paths.add(self.path)
            for resource_spans in request.resource_spans:
                resource = {a.key: _value(a.value) for a in resource_spans.resource.attributes}
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        recorder.spans.append(
                            RecordedSpan(
                                service=str(resource["service.name"]),
                                name=span.name,
                                trace_id=span.trace_id,
                                span_id=span.span_id,
                                parent_span_id=span.parent_span_id,
                                start_ns=span.start_time_unix_nano,
                                end_ns=span.end_time_unix_nano,
                                attributes={a.key: _value(a.value) for a in span.attributes},
                            )
                        )
            body = ExportTraceServiceResponse().SerializeToString()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server.RequestHandlerClass = Handler
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield recorder
    finally:
        server.shutdown()
        server.server_close()


def _value(value: AnyValue) -> object:
    kind = value.WhichOneof("value")
    if kind == "array_value":
        return [_value(v) for v in value.array_value.values]
    return getattr(value, kind or "string_value")


# A model that lists the Customer's accounts, and tries another Customer's, then answers; every
# call reports its tokens.
LISTING_ADAPTERS = """
from langchain_core.messages import AIMessage, ToolMessage


class AccountLister:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        reply = dict(
            usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            response_metadata={"model_name": self.model},
        )
        if isinstance(messages[-1], ToolMessage):
            return AIMessage("You have one account.", **reply)
        calls = [
            {"name": "list_accounts", "args": {"customer_id": customer}, "id": f"call_{customer}"}
            for customer in ("C001", "C002")
        ]
        return AIMessage("", tool_calls=calls, **reply)


ADAPTERS = {"lister": AccountLister}
"""


def test_one_turn_is_one_trace_across_agent_model_gateway_and_downstream(
    backend: FakeBackend, seeded_database_url: str, database_url: str, tmp_path: Path
) -> None:
    adapters_file = tmp_path / "my_adapters.py"
    adapters_file.write_text(LISTING_ADAPTERS)
    tracing = {"OTEL_EXPORTER_OTLP_ENDPOINT": backend.url}

    with start_gateway(seeded_database_url, tracing) as gateway:
        subprocess.run(
            ["mcp-guard-chat", "--person", "C001", "--conversation", "conversation-1"],
            input="Which accounts do I have?\n",
            env={
                **os.environ,
                **tracing,
                "GATEWAY_URL": gateway.url,
                "DATABASE_URL": database_url,
                "MODEL": "lister:lister-1",
                "MODEL_ADAPTERS_FILE": str(adapters_file),
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    # Spans go out in batches every few seconds; the rest must be sent as each process stops.

    assert backend.paths == {"/v1/traces"}
    [turn] = [s for s in backend.spans if s.name == "turn"]
    assert turn.service == "mcp-guard-agent"
    assert turn.attributes["session.id"] == "conversation-1"
    trace = [s for s in backend.spans if s.trace_id == turn.trace_id]
    by_id = {s.span_id: s for s in trace}
    assert all(s.parent_span_id in by_id for s in trace if s is not turn)
    assert all(s.end_ns >= s.start_ns > 0 for s in trace)

    model_calls = [s for s in trace if s.name == "chat lister-1"]
    assert len(model_calls) == 2
    for call in model_calls:
        assert call.service == "mcp-guard-agent"
        assert call.parent_span_id == turn.span_id
        assert call.attributes["gen_ai.operation.name"] == "chat"
        assert call.attributes["gen_ai.provider.name"] == "lister"
        assert call.attributes["gen_ai.request.model"] == "lister-1"
        assert call.attributes["gen_ai.response.model"] == "lister-1"
        assert call.attributes["gen_ai.usage.input_tokens"] == 12
        assert call.attributes["gen_ai.usage.output_tokens"] == 3

    received = [
        s
        for s in trace
        if s.service == "mcp-guard-gateway" and s.name == "tools/call list_accounts"
    ]
    assert len(received) == 2
    for call in received:
        assert by_id[call.parent_span_id].service == "mcp-guard-agent"
        # The Gateway knows who makes the call from their token.
        assert call.attributes["user.id"] == "C001"
        assert call.attributes["user.roles"] == ["customer"]
        assert call.attributes["gateway.decision.layer"] == "cedar"
    [allowed] = [s for s in received if s.attributes["gateway.decision"] == "allow"]
    [denied] = [s for s in received if s.attributes["gateway.decision"] == "deny"]
    assert "gateway.decision.reason" not in allowed.attributes
    assert denied.attributes["gateway.decision.reason"] == "not_found_or_not_yours"
    # Only the allowed call reaches the server.
    [downstream] = [
        s
        for s in trace
        if s.service == "mcp-guard-gateway" and s.name == "MCP send tools/call list_accounts"
    ]
    assert downstream.parent_span_id == allowed.span_id
