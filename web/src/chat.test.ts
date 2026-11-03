import { describe, expect, it } from "vitest";
import { type ChatEvent, emptyChat, step, totals } from "./chat";

const balanceCall: ChatEvent = {
  event: "tool_call",
  data: { call_id: "c1", tool: "get_balance", arguments: { account_id: "A002" } },
};
const denied: ChatEvent = {
  event: "decision",
  data: {
    call_id: "c1",
    decision: "deny",
    reason: "not_found_or_not_yours",
    explanation: "That account is not one this person may see.",
    layer: "cedar",
    audit_id: 1042,
  },
};
const reply = (input: number, output: number, cost: number | null): ChatEvent => ({
  event: "reply",
  data: { text: "I can't see that account.", input_tokens: input, output_tokens: output, estimated_cost: cost },
});

function play(...actions: Parameters<typeof step>[1][]) {
  return actions.reduce(step, emptyChat);
}

describe("a turn in the chat, built from the Agent API's events", () => {
  it("pairs each Tool call with its Decision and ends with the reply", () => {
    const chat = play(
      { type: "ask", text: "Show me C002's balance" },
      { type: "event", event: balanceCall },
      { type: "event", event: denied },
      { type: "event", event: reply(610, 40, 0.0016) },
    );

    expect(chat.turns).toEqual([
      {
        text: "Show me C002's balance",
        steps: [
          {
            callId: "c1",
            tool: "get_balance",
            arguments: { account_id: "A002" },
            decision: {
              decision: "deny",
              reason: "not_found_or_not_yours",
              explanation: "That account is not one this person may see.",
              layer: "cedar",
              auditId: 1042,
            },
          },
        ],
        reply: { text: "I can't see that account.", tokens: 650, cost: 0.0016 },
        error: null,
        running: false,
      },
    ]);
  });

  it("ends the turn with the error's message, and the next message starts a new turn", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: { event: "error", data: { message: "The model is unavailable." } } },
      { type: "ask", text: "Try again" },
    );

    expect(chat.turns.map((t) => [t.text, t.error, t.running])).toEqual([
      ["What's my balance?", "The model is unavailable.", false],
      ["Try again", null, true],
    ]);
  });

  it("ends the turn when its request fails before any reply", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: balanceCall },
      { type: "failed", message: "The connection to the Agent broke off." },
    );

    expect(chat.turns[0]).toMatchObject({
      error: "The connection to the Agent broke off.",
      running: false,
      reply: null,
    });
  });

  it("ignores an event it doesn't know, such as one a newer Agent API sends", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: { event: "keep_alive", data: {} } as unknown as ChatEvent },
    );

    expect(chat.turns[0]).toMatchObject({ text: "What's my balance?", running: true, steps: [] });
  });
});

describe("the conversation's totals", () => {
  it("sums the tokens and the estimated cost of every finished turn", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: reply(600, 50, 0.0017) },
      { type: "ask", text: "And in paise?" },
      { type: "event", event: reply(900, 30, 0.0021) },
    );

    expect(totals(chat)).toEqual({ tokens: 1580, cost: expect.closeTo(0.0038, 10) });
  });

  it("shows only tokens when no price is known", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: reply(600, 50, null) },
    );

    expect(totals(chat)).toEqual({ tokens: 650, cost: null });
  });

  it("shows no cost rather than too low a cost when some turns had no price", () => {
    const chat = play(
      { type: "ask", text: "What's my balance?" },
      { type: "event", event: reply(600, 50, 0.0017) },
      { type: "ask", text: "And in paise?" },
      { type: "event", event: reply(900, 30, null) },
    );

    expect(totals(chat)).toEqual({ tokens: 1580, cost: null });
  });
});
