"""The Gateway's HTTP endpoints for people: a demo token issuer, and who a UI token belongs to.

    POST /demo/login {"person": "C001"}  ->  {"person", "role", "mcp_token", "ui_token"}
    GET  /me  (with the UI token)        ->  {"person", "role"}
    GET  /.well-known/jwks.json          ->  the public key that checks every token

It is a demo issuer: there is no password, so anyone can log in as any person in the seeded
bank (in DATABASE_URL), and unknown people are refused. A real bank would have its own
identity provider sign these tokens instead.
"""

import json
import os
from typing import cast

import psycopg
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_guard.gateway.tokens import Identity, Role, Tokens

LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"

FIND_PERSON = """
SELECT 'customer' FROM bank.customers WHERE id = %(person)s
UNION ALL
SELECT CASE WHEN is_supervisor THEN 'supervisor' ELSE 'staff' END
FROM bank.staff WHERE id = %(person)s
"""


def routes(tokens: Tokens) -> list[Route]:
    async def demo_login(request: Request) -> JSONResponse:
        try:
            body: object = await request.json()
        except json.JSONDecodeError:
            body = None
        person = cast(dict[str, object], body).get("person") if isinstance(body, dict) else None
        if not isinstance(person, str):
            return JSONResponse({"error": 'Send {"person": "<ID>"}'}, status_code=400)
        role = await _role_of(person)
        if role is None:
            return JSONResponse({"error": f"No person with ID {person}"}, status_code=401)
        identity = Identity(person, role)
        return JSONResponse(
            {
                "person": person,
                "role": role,
                "mcp_token": tokens.sign(identity, "mcp"),
                "ui_token": tokens.sign(identity, "ui"),
            }
        )

    async def me(request: Request) -> JSONResponse:
        identity = _ui_identity(request, tokens)
        if identity is None:
            return JSONResponse(
                {"error": "invalid_token", "error_description": "A UI token is required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return JSONResponse({"person": identity.person, "role": identity.role})

    async def jwks(request: Request) -> JSONResponse:
        return JSONResponse(tokens.jwks())

    return [
        Route("/demo/login", demo_login, methods=["POST"]),
        Route("/me", me),
        Route("/.well-known/jwks.json", jwks),
    ]


def _ui_identity(request: Request, tokens: Tokens) -> Identity | None:
    """Who the request's UI token names; None without a valid one (an MCP token isn't one)."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return tokens.check(token, "ui")


async def _role_of(person: str) -> Role | None:
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        row = await (await conn.execute(FIND_PERSON, {"person": person})).fetchone()
    return row[0] if row else None
