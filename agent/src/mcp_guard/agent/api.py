"""The Agent API: what the web app chats through, one step of a turn at a time.

    POST /conversations                  ->  201 {"id"}, a new conversation the person owns
    POST /conversations/{id}/turns {"text"}  ->  a text/event-stream of the turn's steps:
        tool_call  {call_id, tool, arguments}            the model asks for a Tool call
        decision   {call_id, decision, reason, explanation, layer, audit_id}  the Gateway's
                   Decision on that call (call_id pairs it with its tool_call)
        reply      {text, input_tokens, output_tokens, estimated_cost}  the turn's last event
        error      {message}                                  the turn ended without a reply

Every request carries the person's MCP token as `Authorization: Bearer`, from logging in at
the Gateway. The API checks it against the Gateway's public key (its JWKS) and then uses that
same token for the person's Tool calls: the Agent only ever holds MCP tokens (ADR-0005).
A conversation belongs to whoever opened it (kept in `agent.conversations`); anyone else
gets 403. A conversation runs one turn at a time: a second turn while one runs gets 409.
A turn runs to its end even if the browser stops listening, so the stored conversation is
never left half-way through a step.

Each token gets its own Agent with its own Gateway connection, closed after 15 idle minutes.
The model is the one MODEL names (see adapters.py), else the scripted fake model. Tokens are
summed over every model call of a turn and priced at the model's price (see
`configured_price` in adapters.py: MODEL_PRICE like `2,10`, else the listed price); with no
price known, `estimated_cost` is null.

Run it with `make agent-api` (port AGENT_API_PORT, default 8001; GATEWAY_URL and DATABASE_URL
as for the terminal chat).
"""

import json
import logging
import os
import sys
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import anyio
import httpx
import jwt
import psycopg
import uvicorn
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectSendStream
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from mcp_guard.agent.adapters import configured_model, configured_price
from mcp_guard.agent.agent import Agent, Decided, Step, ToolCalled, start_agent
from mcp_guard.agent.cost import Price
from mcp_guard.agent.model import ModelCallFailed
from mcp_guard.agent.person import Person, Role
from mcp_guard.agent.tracing import start_tracing

LOCAL_GATEWAY_URL = "http://127.0.0.1:8000/mcp"
LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"
IDLE_SECONDS = 15 * 60
SHUTDOWN_SECONDS = 3
ROLES: tuple[Role, ...] = ("customer", "staff", "supervisor")
# A Gateway restarted without GATEWAY_SIGNING_KEY has a new key; look it up again, but not
# more often than this.
JWKS_REFRESH_SECONDS = 10
JWKS_TIMEOUT_SECONDS = 2

_log = logging.getLogger(__name__)

type OpenAgent = Callable[[Person, str], AbstractAsyncContextManager[Agent]]
type Event = tuple[str, dict[str, Any]]


class GatewayKeys:
    """Checks MCP tokens against the public key the Gateway at `gateway_url` publishes.

    It checks what the Gateway's own token check does (signature, expiry, audience, issuer,
    role), from the outside: the Agent doesn't share the Gateway's code or key.
    """

    def __init__(self, gateway_url: str) -> None:
        parts = urlsplit(gateway_url)
        self._audience = gateway_url  # an MCP token's audience is the MCP endpoint
        self._issuer = f"{parts.scheme}://{parts.netloc}"
        self._jwks_url = urljoin(gateway_url, "/.well-known/jwks.json")
        self._keys: list[jwt.PyJWK] = []
        self._fetched_at = -JWKS_REFRESH_SECONDS - 1.0  # never: the first check fetches
        self._fetching = anyio.Lock()  # one fetch at a time; the others wait for its keys

    async def person(self, token: str) -> Person | None:
        """Who an MCP token names, if the Gateway signed it, it is unexpired and for MCP."""
        if not token:
            return None
        person = self._check(token)
        if person is not None:
            return person
        async with self._fetching:
            # Not more often than every few seconds, even while the Gateway is down; a check
            # that waited for another's fetch tries the keys that fetch brought.
            if time.monotonic() - self._fetched_at > JWKS_REFRESH_SECONDS:
                await self._fetch()
            return self._check(token)

    def _check(self, token: str) -> Person | None:
        for key in self._keys:
            try:
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["EdDSA"],
                    audience=self._audience,
                    issuer=self._issuer,
                    options={"require": ["exp", "sub", "aud", "iss"]},
                )
            except jwt.InvalidTokenError:
                continue
            role = claims.get("role")
            if role in ROLES:
                return Person(claims["sub"], role)
        return None

    async def _fetch(self) -> None:
        self._fetched_at = time.monotonic()
        try:
            # Short: checks that arrive meanwhile wait for this fetch.
            async with httpx.AsyncClient(timeout=JWKS_TIMEOUT_SECONDS) as http:
                reply = await http.get(self._jwks_url)
            reply.raise_for_status()
            self._keys = [jwt.PyJWK(key) for key in reply.json()["keys"]]
        except (httpx.HTTPError, KeyError, ValueError, jwt.PyJWKError):
            _log.exception("Couldn't read the Gateway's public key at %s", self._jwks_url)


@dataclass
class _OpenAgent:
    """One MCP token's Agent, kept open while it is used."""

    ready: anyio.Event = field(default_factory=anyio.Event)
    agent: Agent | None = None
    failure: BaseException | None = None
    turns: int = 0  # running now
    last_used: float = field(default_factory=time.monotonic)


class AgentPool:
    """One Agent (and Gateway connection) per MCP token, closed after `idle_seconds` unused.

    Each Agent is opened and closed by its own task in `tasks`, which outlives any request.
    """

    def __init__(self, tasks: TaskGroup, open_agent: OpenAgent, idle_seconds: float) -> None:
        self._tasks = tasks
        self._open_agent = open_agent
        self._idle_seconds = idle_seconds
        self._agents: dict[str, _OpenAgent] = {}

    @asynccontextmanager
    async def agent(self, person: Person, token: str) -> AsyncGenerator[Agent]:
        """The token's Agent, opened if it isn't yet, kept open while in use."""
        session = self._agents.get(token)
        if session is None:
            session = self._agents[token] = _OpenAgent()
            self._tasks.start_soon(self._keep_open, person, token, session)
        session.turns += 1
        try:
            await session.ready.wait()
            if session.agent is None:
                raise RuntimeError("The Agent couldn't start") from session.failure
            yield session.agent
        finally:
            session.turns -= 1
            session.last_used = time.monotonic()

    async def _keep_open(self, person: Person, token: str, session: _OpenAgent) -> None:
        try:
            async with self._open_agent(person, token) as agent:
                session.agent = agent
                session.ready.set()
                while True:
                    idle_until = session.last_used + self._idle_seconds
                    if session.turns == 0 and time.monotonic() >= idle_until:
                        # Forgotten before it closes, so no new turn gets a closing Agent.
                        self._forget(token, session)
                        return
                    await anyio.sleep(max(idle_until - time.monotonic(), 1))
        except Exception as e:  # the Gateway or the store is unreachable, say
            _log.exception("The Agent for %s couldn't start or stopped", person.id)
            session.failure = e
        finally:
            self._forget(token, session)
            session.ready.set()

    def _forget(self, token: str, session: _OpenAgent) -> None:
        if self._agents.get(token) is session:
            del self._agents[token]


class Conversations:
    """Who owns each conversation, in `agent.conversations`."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def setup(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._database_url) as db:
            await db.execute("CREATE SCHEMA IF NOT EXISTS agent")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS agent.conversations ("
                " id text PRIMARY KEY,"
                " owner text NOT NULL,"
                " created_at timestamptz NOT NULL DEFAULT now())"
            )

    async def open(self, owner: Person) -> str:
        conversation_id = str(uuid.uuid4())
        async with await psycopg.AsyncConnection.connect(self._database_url) as db:
            await db.execute(
                "INSERT INTO agent.conversations (id, owner) VALUES (%s, %s)",
                (conversation_id, owner.id),
            )
        return conversation_id

    async def owner(self, conversation_id: str) -> str | None:
        async with await psycopg.AsyncConnection.connect(self._database_url) as db:
            cursor = await db.execute(
                "SELECT owner FROM agent.conversations WHERE id = %s", (conversation_id,)
            )
            row = await cursor.fetchone()
        return None if row is None else str(row[0])


def app(
    gateway_url: str,
    database_url: str,
    open_agent: OpenAgent,
    price: Price | None,
    idle_seconds: float = IDLE_SECONDS,
) -> Starlette:
    keys = GatewayKeys(gateway_url)
    conversations = Conversations(database_url)
    running: set[str] = set()  # conversations with a turn under way
    agents: AgentPool | None = None
    tasks: TaskGroup | None = None

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncGenerator[None]:
        nonlocal agents, tasks
        await conversations.setup()
        async with anyio.create_task_group() as group:
            tasks = group
            agents = AgentPool(group, open_agent, idle_seconds)
            yield
            group.cancel_scope.cancel()

    async def person_of(request: Request) -> tuple[Person, str] | None:
        """The person the request's MCP token names, and the token."""
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return None
        person = await keys.person(token)
        return None if person is None else (person, token)

    async def open_conversation(request: Request) -> Response:
        person_and_token = await person_of(request)
        if person_and_token is None:
            return _mcp_token_required()
        person, _ = person_and_token
        return JSONResponse({"id": await conversations.open(person)}, status_code=201)

    async def take_turn(request: Request) -> Response:
        person_and_token = await person_of(request)
        if person_and_token is None:
            return _mcp_token_required()
        person, token = person_and_token
        conversation_id = request.path_params["id"]
        try:
            body: object = await request.json()
        except json.JSONDecodeError:
            body = None
        text = cast(dict[str, object], body).get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": 'Send {"text": "<what the person says>"}'}, 400)
        owner = await conversations.owner(conversation_id)
        if owner is None:
            return JSONResponse({"error": "No such conversation"}, 404)
        if owner != person.id:
            return JSONResponse({"error": "This conversation isn't yours"}, 403)
        if conversation_id in running:
            return JSONResponse({"error": "A turn is already running in this conversation"}, 409)
        assert agents is not None and tasks is not None
        running.add(conversation_id)
        send, receive = anyio.create_memory_object_stream[Event](float("inf"))
        turn = _Turn(person, token, conversation_id, text)
        # Run beside the request, not in it, so the browser leaving doesn't stop the turn.
        tasks.start_soon(_run_turn, agents, turn, price, send, running.discard)

        async def stream() -> AsyncIterator[str]:
            with receive:
                async for name, data in receive:
                    yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return Starlette(
        routes=[
            Route("/conversations", open_conversation, methods=["POST"]),
            Route("/conversations/{id}/turns", take_turn, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


@dataclass(frozen=True)
class _Turn:
    person: Person
    token: str
    conversation_id: str
    text: str


async def _run_turn(
    agents: AgentPool,
    turn: _Turn,
    price: Price | None,
    send: MemoryObjectSendStream[Event],
    done: Callable[[str], None],
) -> None:
    """Run one turn, sending each step to `send` (the browser may have stopped listening),
    then tell `done` its conversation is free for the next turn."""

    async def emit(name: str, data: dict[str, Any]) -> None:
        # If nobody is listening any more, the turn carries on all the same.
        with suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
            send.send_nowait((name, data))

    async def on_step(step: Step) -> None:
        match step:
            case ToolCalled():
                await emit("tool_call", asdict(step))
            case Decided():
                await emit("decision", asdict(step))

    with send:
        try:
            async with agents.agent(turn.person, turn.token) as agent:
                reply = await agent.turn(turn.conversation_id, turn.text, on_step)
            cost = None if price is None else price.cost(reply.input_tokens, reply.output_tokens)
            await emit("reply", asdict(reply) | {"estimated_cost": cost})
        except ModelCallFailed as e:
            await emit("error", {"message": str(e)})
        except Exception:
            _log.exception("A turn of conversation %s failed", turn.conversation_id)
            await emit("error", {"message": "Something went wrong; please try again."})
        finally:
            done(turn.conversation_id)


def _mcp_token_required() -> JSONResponse:
    return JSONResponse(
        {
            "error": "invalid_token",
            "error_description": "An MCP token from the Gateway is required",
        },
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def _open_agent(gateway_url: str, database_url: str) -> OpenAgent:
    @asynccontextmanager
    async def open_agent(person: Person, mcp_token: str) -> AsyncGenerator[Agent]:
        model_name, model = configured_model(person.id)
        async with start_agent(
            model,
            gateway_url,
            database_url,
            person=person,
            mcp_token=mcp_token,
            model_name=model_name,
        ) as agent:
            yield agent

    return open_agent


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    gateway_url = os.environ.get("GATEWAY_URL", LOCAL_GATEWAY_URL)
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    port = int(os.environ.get("AGENT_API_PORT", "8001"))
    try:
        price = configured_price()
    except ValueError as e:
        sys.exit(f"The model's price: {e}")
    start_tracing("mcp-guard-agent")
    api = app(gateway_url, database_url, _open_agent(gateway_url, database_url), price)
    uvicorn.run(api, host="127.0.0.1", port=port, timeout_graceful_shutdown=SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
