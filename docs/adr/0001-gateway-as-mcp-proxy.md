# The Gateway is an MCP proxy server

The Gateway presents itself to an Agent as a single MCP server and forwards each Tool call to the real MCP servers behind it. Every Tool call crosses one process boundary that the Agent can't skip, so authentication, policy, redaction, Holds and the audit log all live in one place, and any MCP client (our LangGraph Agent, desktop chat apps, IDEs) is protected without code changes.

## Considered Options

- **A library inside the Agent:** simpler and lower latency, but it protects only our own Agent, and a compromised or misconfigured Agent can bypass it. A guard the attacker's target runs isn't a boundary.
- **A network proxy on raw HTTP:** agent-agnostic, but it sees bytes, not MCP messages, so it can't reason about tool names, arguments or tool definitions (needed for tool poisoning and rug pulls).

## Consequences

- The Agent talks only to the Gateway; downstream MCP servers aren't reachable from the Agent's network.
- The Gateway must implement the MCP server side (tool listing, calls, OAuth 2.1 resource-server role) and the MCP client side towards each downstream server.
- Tool definitions pass through the Gateway, which makes pinning and diffing them possible.
