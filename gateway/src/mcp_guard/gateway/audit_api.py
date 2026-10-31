"""The Audit log over the Gateway's HTTP API, for Supervisors only.

    GET /audit?before=<id>&limit=50  ->  {"entries": [...]}
    GET /audit/verify                ->  {"intact": true, "entries": N}
                                         or {"intact": false, "first_broken": <id>}

Both need a Supervisor's UI token.

Entries come newest first; pass the last one's id as `before` for the next page. Without a
UI token the answer is 401; with another role's, 403.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_guard.gateway import audit
from mcp_guard.gateway.login import ui_person, ui_token_required
from mcp_guard.gateway.tokens import Tokens


def routes(tokens: Tokens) -> list[Route]:
    def refusal(request: Request) -> JSONResponse | None:
        """Why the request may not read the Audit log, or None if a Supervisor sent it."""
        person = ui_person(request, tokens)
        if person is None:
            return ui_token_required()
        if person.role != "supervisor":
            return JSONResponse(
                {
                    "error": "forbidden",
                    "error_description": "Only a Supervisor reads the Audit log",
                },
                status_code=403,
            )
        return None

    async def read_page(request: Request) -> JSONResponse:
        if refused := refusal(request):
            return refused
        try:
            before = _whole_number(request.query_params.get("before"))
            limit = _whole_number(request.query_params.get("limit")) or audit.PAGE_SIZE
        except ValueError:
            return JSONResponse(
                {"error": "before and limit must be whole numbers above 0"}, status_code=400
            )
        entries = await audit.read(before, min(limit, audit.PAGE_SIZE))
        return JSONResponse({"entries": [e.to_json() for e in entries]})

    async def verify(request: Request) -> JSONResponse:
        if refused := refusal(request):
            return refused
        return JSONResponse((await audit.verify()).to_json())

    return [Route("/audit", read_page), Route("/audit/verify", verify)]


def _whole_number(text: str | None) -> int | None:
    if text is None:
        return None
    number = int(text)
    if number < 1:
        raise ValueError(text)
    return number
