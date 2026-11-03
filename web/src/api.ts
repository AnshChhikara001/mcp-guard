/**
 * The two backends, through Vite's proxy in development (see vite.config.ts):
 * /gateway/* is the Gateway, /agent/* the Agent API.
 *
 * The UI token stays in the browser for the Gateway's endpoints for people; only the MCP token
 * goes to the Agent API (ADR-0005).
 */

export type Role = "customer" | "staff" | "supervisor";

export type Login = { person: string; role: Role; mcpToken: string; uiToken: string };
export type Layer = { id: string; name: string };

/** The token was refused (expired, say): log in again and retry. */
export class Unauthorized extends Error {}

/** A request the server refused, with its own words for why. */
export class Refused extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function check(reply: Response): Promise<Response> {
  if (reply.ok) return reply;
  if (reply.status === 401) throw new Unauthorized();
  let message = `The server answered ${reply.status}.`;
  try {
    const body = (await reply.json()) as { error?: unknown };
    if (typeof body.error === "string") message = body.error;
  } catch {
    // not JSON: keep the status
  }
  throw new Refused(reply.status, message);
}

const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });
const json = { "Content-Type": "application/json" };

export async function login(person: string): Promise<Login> {
  const reply = await check(
    await fetch("/gateway/demo/login", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ person }),
    }),
  );
  const body = (await reply.json()) as {
    person: string;
    role: Role;
    mcp_token: string;
    ui_token: string;
  };
  return {
    person: body.person,
    role: body.role,
    mcpToken: body.mcp_token,
    uiToken: body.ui_token,
  };
}

/** Who the UI token names, as the Gateway sees it. */
export async function me(uiToken: string): Promise<{ person: string; role: Role }> {
  const reply = await check(await fetch("/gateway/me", { headers: bearer(uiToken) }));
  return (await reply.json()) as { person: string; role: Role };
}

/** The Gateway's checks that are switched on, in the order a Tool call meets them. */
export async function layers(): Promise<Layer[]> {
  const reply = await check(await fetch("/gateway/layers"));
  return (await reply.json()) as Layer[];
}

export async function openConversation(mcpToken: string): Promise<string> {
  const reply = await check(
    await fetch("/agent/conversations", { method: "POST", headers: bearer(mcpToken) }),
  );
  return ((await reply.json()) as { id: string }).id;
}

/** Starts a turn; its body is the turn's event stream (see chat.ts for the events). */
export async function takeTurn(
  mcpToken: string,
  conversationId: string,
  message: { text: string; suggested?: true },
  signal: AbortSignal,
): Promise<ReadableStream<Uint8Array>> {
  const reply = await check(
    await fetch(`/agent/conversations/${encodeURIComponent(conversationId)}/turns`, {
      method: "POST",
      headers: { ...json, ...bearer(mcpToken) },
      body: JSON.stringify(message),
      signal,
    }),
  );
  if (!reply.body) throw new Refused(reply.status, "The Agent sent no steps.");
  return reply.body;
}
