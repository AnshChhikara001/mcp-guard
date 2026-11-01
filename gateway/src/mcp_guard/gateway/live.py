"""What the web app draws live, over the Gateway's HTTP API.

    GET /layers  ->  [{"id": "cedar", "name": "Permissions"}, ...]
    GET /events  (with the UI token)  ->  text/event-stream, one SSE event per new Audit entry

`/layers` names the checks switched on, in the order a Tool call meets them; with the
defences off there are none. It needs no token: it says how the Gateway is set up, not what
anyone did. The Gateway's own denies (an unknown tool) carry the layer `gateway`, which is
not a check and isn't listed.

`/events` sends each new Audit entry as it is written: id, time, person, role, conversation,
tool, arguments, decision, reason, layer and hash, plus two things the log doesn't store: the
`server` that owns the tool (null for an unknown tool) and the `explanation` of a deny. A
Customer or Staff member gets only their own entries; a Supervisor gets everyone's. It opens
with a comment line once the viewer is subscribed, and sends nothing older: a browser that
reconnects with `Last-Event-ID` first gets the entries it missed, read back from the log. The
UI token goes in the `Authorization` header, never the URL, so the web app reads the stream
with fetch.

The Feed writes each entry and hands it to viewers in one step, so viewers get entries in the
order they are numbered. Entries reach viewers through memory, so only viewers of this Gateway
process see them. A viewer that falls too far behind is cut off, and catches up by
reconnecting.
"""

import json
import re
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from mcp_guard.gateway import audit
from mcp_guard.gateway.audit import Entry
from mcp_guard.gateway.decisions import Decision
from mcp_guard.gateway.login import ui_person, ui_token_required
from mcp_guard.gateway.tokens import Person, Tokens

# Entries a viewer may fall behind by before it is cut off.
BACKLOG = 100
# A comment is sent this often on a quiet stream, so proxies don't close it.
KEEPALIVE_SECONDS = 15
# A Last-Event-ID the log could hold: digits that fit Postgres' bigint.
ENTRY_ID = re.compile(r"[0-9]{1,18}")


@dataclass(frozen=True)
class Layer:
    """One check a Tool call goes through: its id in Decisions, and its name on screen."""

    id: str
    name: str


@dataclass(frozen=True)
class LiveEntry:
    """One Audit entry as `/events` sends it: with its server and the deny's explanation."""

    entry: Entry
    server: str | None
    explanation: str | None

    def to_sse(self) -> str:
        data = self.entry.to_json() | {"server": self.server, "explanation": self.explanation}
        del data["previous_hash"]
        return f"id: {self.entry.id}\nevent: audit\ndata: {json.dumps(data)}\n\n"


type Explain = Callable[[Entry], str | None]  # a deny's explanation, rebuilt from its entry


class Feed:
    """Writes each Decision to the Audit log, and hands the new entry to everyone watching.

    `explain` gives a deny's explanation from its entry, for live entries and for the ones
    read back from the log alike.
    """

    def __init__(self, explain: Explain) -> None:
        self._explain = explain
        self._server_of: Callable[[str], str | None] = lambda tool: None
        self._viewers: set[MemoryObjectSendStream[LiveEntry]] = set()
        self._writing = anyio.Lock()

    def learn_servers(self, server_of: Callable[[str], str | None]) -> None:
        """Which server owns each tool, once the Gateway has connected to them."""
        self._server_of = server_of

    async def record(
        self,
        person: Person,
        conversation: str | None,
        tool: str,
        arguments: dict[str, Any] | None,
        decision: Decision,
    ) -> Entry:
        """Append the Decision to the Audit log (see audit.record) and send the new entry."""
        async with self._writing:
            entry = await audit.record(person, conversation, tool, arguments, decision)
            self._publish(self.live(entry))
        return entry

    def live(self, entry: Entry) -> LiveEntry:
        return LiveEntry(entry, self._server_of(entry.tool), self._explain(entry))

    def _publish(self, live_entry: LiveEntry) -> None:
        for viewer in list(self._viewers):
            try:
                viewer.send_nowait(live_entry)
            except (anyio.WouldBlock, anyio.BrokenResourceError):
                # Too far behind (or gone): cut off, so it reconnects and catches up.
                viewer.close()
                self._viewers.discard(viewer)

    @contextmanager
    def subscribe(self) -> Generator[MemoryObjectReceiveStream[LiveEntry]]:
        send, receive = anyio.create_memory_object_stream[LiveEntry](BACKLOG)
        self._viewers.add(send)
        try:
            with receive:
                yield receive
        finally:
            self._viewers.discard(send)
            send.close()


def routes(tokens: Tokens, layers: Sequence[Layer], feed: Feed) -> list[Route]:
    async def list_layers(request: Request) -> JSONResponse:
        return JSONResponse([asdict(layer) for layer in layers])

    async def stream_entries(request: Request) -> Response:
        viewer = ui_person(request, tokens)
        if viewer is None:
            return ui_token_required()
        last_event_id = request.headers.get("Last-Event-ID")
        if last_event_id is not None and not ENTRY_ID.fullmatch(last_event_id):
            return JSONResponse({"error": "Last-Event-ID must be an Audit entry's id"}, 400)
        return StreamingResponse(
            _stream(feed, viewer, None if last_event_id is None else int(last_event_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return [Route("/layers", list_layers), Route("/events", stream_entries)]


def _only_person(viewer: Person) -> str | None:
    """Whose entries `viewer` may see: their own, or everyone's (None) for a Supervisor."""
    return None if viewer.role == "supervisor" else viewer.id


async def _stream(feed: Feed, viewer: Person, last_event_id: int | None) -> AsyncGenerator[str]:
    only = _only_person(viewer)
    with feed.subscribe() as new_entries:
        yield ": subscribed\n\n"
        last_sent_id = 0
        if last_event_id is not None:
            last_sent_id = last_event_id
            # Page through what was missed, so a long gap isn't read into memory at once.
            while missed := await audit.read_after(last_sent_id, only):
                for entry in missed:
                    yield feed.live(entry).to_sse()
                last_sent_id = missed[-1].id
        while True:
            live_entry: LiveEntry | None = None
            with anyio.move_on_after(KEEPALIVE_SECONDS):
                try:
                    live_entry = await new_entries.receive()
                except anyio.EndOfStream:
                    return
            if live_entry is None:
                yield ": keep-alive\n\n"
            elif live_entry.entry.id > last_sent_id and only in (None, live_entry.entry.person):
                yield live_entry.to_sse()
