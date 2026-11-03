import { describe, expect, it } from "vitest";
import { readEvents } from "./sse";

/** A response body that arrives in these exact byte chunks. */
function body(...chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function all(stream: ReadableStream<Uint8Array>) {
  const found = [];
  for await (const event of readEvents(stream)) found.push(event);
  return found;
}

describe("reading a Server-Sent Events stream", () => {
  it("yields each event's name and data, however the bytes are split", async () => {
    const bytes = new TextEncoder().encode(
      'event: tool_call\ndata: {"tool": "get_balance"}\n\n' +
        ": a comment line\n\n" +
        'event: reply\ndata: {"text": "₹48,210.55"}\n\n',
    );
    const split = bytes.indexOf(0xe2) + 1; // inside the three bytes of "₹"

    const events = await all(body(bytes.slice(0, 20), bytes.slice(20, split), bytes.slice(split)));

    expect(events).toEqual([
      { event: "tool_call", data: { tool: "get_balance" } },
      { event: "reply", data: { text: "₹48,210.55" } },
    ]);
  });
});
