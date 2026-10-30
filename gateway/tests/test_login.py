"""Seam 1: people log in to the Gateway's demo issuer, and every request must carry their token.

Each person gets two tokens that differ by audience (ADR-0005): an MCP token, accepted only on
the MCP endpoint, and a UI token, accepted only on the Gateway's HTTP endpoints.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from conftest import RunningGateway
from mcp_guard.gateway.process import start_gateway
from mcp_guard.gateway.tokens import Audience, Person, Tokens, signing_key


@pytest.mark.parametrize(
    ("person", "role"),
    [("C001", "customer"), ("S001", "staff"), ("S004", "supervisor")],
)
def test_anyone_in_the_bank_logs_in_and_learns_their_role(
    gateway: RunningGateway, person: str, role: str
) -> None:
    reply = httpx.post(f"{gateway.base_url}/demo/login", json={"person": person})

    assert reply.status_code == 200
    body = reply.json()
    assert (body["person"], body["role"]) == (person, role)
    assert body["mcp_token"] != body["ui_token"]


def test_someone_the_bank_doesnt_know_is_refused(gateway: RunningGateway) -> None:
    reply = httpx.post(f"{gateway.base_url}/demo/login", json={"person": "C999"})

    assert reply.status_code == 401
    assert "mcp_token" not in reply.json()


def mcp_request(gateway: RunningGateway, token: str | None = None) -> httpx.Response:
    """The opening request an MCP client sends, with `token` as its bearer token."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    return httpx.post(gateway.url, json=initialize, headers=headers)


def login(gateway: RunningGateway, person: str) -> dict[str, str]:
    return httpx.post(f"{gateway.base_url}/demo/login", json={"person": person}).json()


def test_a_call_without_a_token_is_refused_and_told_where_to_learn_about_auth(
    gateway: RunningGateway,
) -> None:
    reply = mcp_request(gateway)

    assert reply.status_code == 401
    [metadata_url] = re.findall(r'resource_metadata="([^"]+)"', reply.headers["WWW-Authenticate"])
    assert metadata_url == f"{gateway.base_url}/.well-known/oauth-protected-resource/mcp"
    metadata = httpx.get(metadata_url).json()
    assert metadata["resource"] == gateway.url
    assert metadata["authorization_servers"] == [gateway.base_url]


def test_the_mcp_token_opens_the_mcp_endpoint(gateway: RunningGateway) -> None:
    reply = mcp_request(gateway, login(gateway, "C001")["mcp_token"])

    assert reply.status_code == 200


def test_the_ui_token_says_who_it_belongs_to(gateway: RunningGateway) -> None:
    token = login(gateway, "S004")["ui_token"]

    reply = httpx.get(f"{gateway.base_url}/me", headers={"Authorization": f"Bearer {token}"})

    assert reply.status_code == 200
    assert reply.json() == {"person": "S004", "role": "supervisor"}


@pytest.mark.parametrize("token", ["mcp_token", None])
def test_the_http_endpoints_refuse_an_mcp_token_or_none(
    gateway: RunningGateway, token: str | None
) -> None:
    headers = {"Authorization": f"Bearer {login(gateway, 'S004')[token]}"} if token else {}

    reply = httpx.get(f"{gateway.base_url}/me", headers=headers)

    assert reply.status_code == 401
    assert reply.headers["WWW-Authenticate"].startswith("Bearer")


def test_anyone_can_check_a_token_with_the_published_public_key(gateway: RunningGateway) -> None:
    token = login(gateway, "C001")["mcp_token"]

    [key] = httpx.get(f"{gateway.base_url}/.well-known/jwks.json").json()["keys"]
    claims = jwt.decode(token, jwt.PyJWK(key), algorithms=["EdDSA"], audience=gateway.url)

    assert (claims["sub"], claims["role"], claims["iss"]) == ("C001", "customer", gateway.base_url)
    assert claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize("body", ["{}", '{"person": 1}', "not json"])
def test_a_login_without_a_person_is_a_bad_request(gateway: RunningGateway, body: str) -> None:
    reply = httpx.post(f"{gateway.base_url}/demo/login", content=body)

    assert reply.status_code == 400


# A fixed signing key, as a deployment sets it, so tests can sign tokens of their own.
SIGNING_KEY = "nWGxne_9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A"


@pytest.fixture(scope="module")
def keyed_gateway(seeded_database_url: str) -> Iterator[RunningGateway]:
    with start_gateway(seeded_database_url, {"GATEWAY_SIGNING_KEY": SIGNING_KEY}) as running:
        yield running


def signed(
    gateway: RunningGateway,
    audience: Audience = "mcp",
    *,
    key: str = SIGNING_KEY,
    issued: timedelta = timedelta(0),
) -> str:
    """A token for Customer C001, signed with `key`, issued `issued` ago."""
    tokens = Tokens(signing_key(key), gateway.base_url)
    return tokens.sign(Person("C001", "customer"), audience, now=datetime.now(UTC) - issued)


def test_a_token_signed_with_the_configured_key_is_accepted(
    keyed_gateway: RunningGateway,
) -> None:
    assert mcp_request(keyed_gateway, signed(keyed_gateway)).status_code == 200


OTHER_KEY = "Ui4pLCTrpgTyMOjVMEnhBAFEC7xT6xLC6eMSzO6mQLE"


@pytest.mark.parametrize(
    ("audience", "key", "issued"),
    [
        ("mcp", SIGNING_KEY, timedelta(minutes=61)),
        ("mcp", OTHER_KEY, timedelta(0)),
        ("ui", SIGNING_KEY, timedelta(0)),
    ],
    ids=["expired", "bad-signature", "ui-token"],
)
def test_the_mcp_endpoint_refuses_a_token_that_is_not_a_live_mcp_token_of_ours(
    keyed_gateway: RunningGateway, audience: Audience, key: str, issued: timedelta
) -> None:
    reply = mcp_request(keyed_gateway, signed(keyed_gateway, audience, key=key, issued=issued))

    assert reply.status_code == 401
    assert 'error="invalid_token"' in reply.headers["WWW-Authenticate"]


def test_the_mcp_endpoint_refuses_something_that_is_not_a_token(
    keyed_gateway: RunningGateway,
) -> None:
    assert mcp_request(keyed_gateway, "not-a-token").status_code == 401


def test_an_unusable_signing_key_stops_the_gateway_at_startup(seeded_database_url: str) -> None:
    with (
        pytest.raises(RuntimeError, match="exited during startup"),
        start_gateway(seeded_database_url, {"GATEWAY_SIGNING_KEY": "too-short"}),
    ):
        pass
