"""Login tokens: JWTs naming a person and their role, signed by the Gateway with Ed25519.

Each person gets two tokens that differ only by audience (ADR-0005): an MCP token for the MCP
endpoint, whose audience is that endpoint's URL as the MCP authorization spec asks, and a UI
token for the Gateway's HTTP endpoints, whose audience is the Gateway's base URL. A token for
one audience is refused on the other. Tokens last an hour and can't be refreshed.

GATEWAY_SIGNING_KEY is the private key, as 32 random bytes in base64url; without it, each
process makes a fresh key, so tokens die with it. The public key is published as a JWKS.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from mcp.server.auth.provider import AccessToken

TOKEN_LIFETIME = timedelta(hours=1)

type Role = Literal["customer", "staff", "supervisor"]
type Audience = Literal["mcp", "ui"]

ROLES: tuple[Role, ...] = ("customer", "staff", "supervisor")


@dataclass(frozen=True)
class Person:
    """Who a token was issued to: a Customer, Staff member or Supervisor (C001, S003)."""

    id: str
    role: Role


def signing_key(encoded: str | None) -> Ed25519PrivateKey:
    """The key GATEWAY_SIGNING_KEY's value encodes, or a fresh one if it is unset."""
    if not encoded:
        return Ed25519PrivateKey.generate()
    try:
        seed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        return Ed25519PrivateKey.from_private_bytes(seed)
    except (binascii.Error, ValueError) as e:
        raise ValueError("GATEWAY_SIGNING_KEY must be 32 bytes in base64url") from e


class Tokens:
    """Signs and checks the tokens of the Gateway at `base_url` (http://host:port)."""

    def __init__(self, key: Ed25519PrivateKey, base_url: str) -> None:
        self._key = key
        self.issuer = base_url
        self.mcp_url = f"{base_url}/mcp"

    def audience(self, audience: Audience) -> str:
        return self.mcp_url if audience == "mcp" else self.issuer

    def sign(self, person: Person, audience: Audience, now: datetime | None = None) -> str:
        issued_at = now or datetime.now(UTC)
        claims = {
            "iss": self.issuer,
            "aud": self.audience(audience),
            "sub": person.id,
            "role": person.role,
            "iat": issued_at,
            "exp": issued_at + TOKEN_LIFETIME,
        }
        return jwt.encode(claims, self._key, algorithm="EdDSA")

    def check(self, token: str, audience: Audience) -> Person | None:
        """Who the token names, if it is ours, unexpired and for `audience`; else None."""
        try:
            claims = jwt.decode(
                token,
                self._key.public_key(),
                algorithms=["EdDSA"],
                audience=self.audience(audience),
                issuer=self.issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.InvalidTokenError:
            return None
        role = claims.get("role")
        if role not in ROLES:
            return None
        return Person(claims["sub"], role)

    def jwks(self) -> dict[str, Any]:
        """The public key, for anyone checking a token."""
        jwk = OKPAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        return {"keys": [jwk | {"alg": "EdDSA", "use": "sig"}]}


class McpTokenVerifier:
    """Accepts MCP tokens only, for the MCP SDK's bearer auth on the MCP endpoint."""

    def __init__(self, tokens: Tokens) -> None:
        self._tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        person = self._tokens.check(token, "mcp")
        if person is None:
            return None
        return AccessToken(
            token=token,
            client_id=person.id,
            scopes=[],
            subject=person.id,
            claims={"iss": self._tokens.issuer, "role": person.role},
        )


def person_of(access_token: AccessToken) -> Person:
    """The person and role of an MCP token McpTokenVerifier accepted."""
    role: Role = (access_token.claims or {})["role"]
    return Person(access_token.subject or access_token.client_id, role)
