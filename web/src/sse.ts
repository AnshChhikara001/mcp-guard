/**
 * Reads a Server-Sent Events body (from `fetch`, so the request can carry a token in its
 * Authorization header, which the browser's EventSource can't).
 */

export type ServerEvent = { event: string; data: unknown; id?: string };

export async function* readEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ServerEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder(); // with `stream`, it keeps a split character whole
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const event = parse(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (event) yield event;
      }
    }
  } finally {
    // The reader stopped early (the page moved on) or the body ended: let the request go.
    await reader.cancel().catch(() => {});
  }
}

function parse(block: string): ServerEvent | null {
  let name = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // a comment, such as a keep-alive
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }
  if (!data.length) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data.join("\n"));
  } catch {
    return null; // not one of ours (a proxy's page, say): skip it
  }
  const event: ServerEvent = { event: name, data: parsed };
  if (id !== undefined) event.id = id;
  return event;
}
