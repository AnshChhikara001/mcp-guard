"""The Gateway's HTTP endpoints for people: a demo token issuer, and who a UI token belongs to.

    POST /demo/login {"person": "C001"}  ->  {"person", "role", "mcp_token", "ui_token"}
    GET  /me  (with the UI token)        ->  {"person", "role"}
    GET  /.well-known/jwks.json          ->  the public key that checks every token

It is a demo issuer: there is no password, so anyone can log in as any person in the seeded
bank (in DATABASE_URL), and unknown people are refused. A real bank would have its own
identity provider sign these tokens instead.
"""

import json
from typing import cast

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_guard.gateway import bank
from mcp_guard.gateway.tokens import Person, Tokens


def routes(tokens: Tokens) -> list[Route]:
    async def demo_login(request: Request) -> JSONResponse:
        try:
            body: object = await request.json()
        except json.JSONDecodeError:
            body = None
        person_id = cast(dict[str, object], body).get("person") if isinstance(body, dict) else None
        if not isinstance(person_id, str):
            return JSONResponse({"error": 'Send {"person": "<ID>"}'}, status_code=400)
        role = await bank.role_of(person_id)
        if role is None:
            return JSONResponse({"error": f"No person with ID {person_id}"}, status_code=401)
        person = Person(person_id, role)
        return JSONResponse(
            {
                "person": person.id,
                "role": person.role,
                "mcp_token": tokens.sign(person, "mcp"),
                "ui_token": tokens.sign(person, "ui"),
            }
        )

    async def me(request: Request) -> JSONResponse:
        person = _ui_person(request, tokens)
        if person is None:
            return JSONResponse(
                {"error": "invalid_token", "error_description": "A UI token is required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return JSONResponse({"person": person.id, "role": person.role})

    async def jwks(request: Request) -> JSONResponse:
        return JSONResponse(tokens.jwks())

    return [
        Route("/demo/login", demo_login, methods=["POST"]),
        Route("/me", me),
        Route("/.well-known/jwks.json", jwks),
    ]


def _ui_person(request: Request, tokens: Tokens) -> Person | None:
    """Who the request's UI token names; None without a valid one (an MCP token isn't one)."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return tokens.check(token, "ui")
