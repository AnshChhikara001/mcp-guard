# Token audience separates the Agent from the chat UI

The Agent forwards the person's own token to the Gateway, so a token alone can't tell an Agent request from a chat UI request, yet the chat UI must be able to reveal Pseudonyms and approve Holds while the Agent never can. The demo token issuer therefore gives each person two tokens that differ by audience (the JWT `aud` claim): an MCP token, accepted only on the Gateway's MCP endpoint and the only one the Agent ever receives, and a UI token, accepted only on the Gateway's HTTP endpoints (reveal, Holds, audit log, tool-definition review) and held only by the chat UI. A prompt-injected Agent holding the MCP token can call tools, within Cedar policy, but can't unmask PII or approve its own Hold.

## Considered Options

- **One token with scopes (`mcp`, `reveal`):** the Agent would carry the reveal scope too, since it forwards the whole token.
- **Identify the Agent by its own client credentials or network origin:** more moving parts, and both the Agent and the browser reach the Gateway through the same Caddy entry point.

## Consequences

- The Gateway checks the audience on every route; a token for one audience is refused on the other.
- The chat UI calls the Gateway's HTTP endpoints directly with the UI token, never through the Agent, so the Gateway allows the frontend's origin (CORS).
- The Gateway never forwards either token to downstream MCP servers.
