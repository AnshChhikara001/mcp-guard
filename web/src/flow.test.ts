import { describe, expect, it } from "vitest";
import type { Layer } from "./api";
import { type Action, type Decision, emptyChat, step } from "./chat";
import { serverOf, storyOf, stripOf } from "./flow";

describe("the bank server that owns each tool", () => {
  it("names the fake bank's server for every tool", () => {
    expect(serverOf("get_balance")).toBe("accounts");
    expect(serverOf("list_transactions")).toBe("transactions");
    expect(serverOf("refund")).toBe("payments");
    expect(serverOf("get_ticket")).toBe("tickets");
    expect(serverOf("send_sms")).toBe("notify");
    expect(serverOf("convert")).toBe("fx-rates");
  });

  it("has no server for a tool no bank server offers", () => {
    expect(serverOf("drop_tables")).toBeNull();
    expect(serverOf("constructor")).toBeNull();
  });
});

const PERMISSIONS: Layer[] = [{ id: "cedar", name: "Permissions" }];

const askC002: Action = { type: "ask", text: "Show me Rohan Mehta's balance (C002)" };
const balanceCall = (callId: string, account: string): Action => ({
  type: "event",
  event: { event: "tool_call", data: { call_id: callId, tool: "get_balance", arguments: { account_id: account } } },
});
const decided = (callId: string, decision: Decision, layer: string, explanation: string | null = null): Action => ({
  type: "event",
  event: {
    event: "decision",
    data: { call_id: callId, decision, reason: null, explanation, layer, audit_id: 7 },
  },
});
const play = (...actions: Action[]) => actions.reduce(step, emptyChat);

describe("the strip, built from the newest Tool call of the chat", () => {
  it("stops a denied Tool call with a ✕ at the check that denied it", () => {
    const chat = play(
      askC002,
      balanceCall("c1", "A002"),
      decided("c1", "deny", "cedar", "That account is not one this person may see."),
    );

    const strip = stripOf(chat, PERMISSIONS);

    expect(strip.status).toBe("decided");
    if (strip.status !== "decided") return;
    expect(strip.route).toEqual([{ at: "you" }, { at: "agent" }, { at: "entrance" }, { at: "check", index: 0 }]);
    expect(strip.end).toBe("cross");
    expect(strip.checks).toEqual(["blocked"]);
    expect(strip.server).toBe("accounts");
  });

  it("takes an allowed Tool call past every check to a dot at its bank server", () => {
    const twoChecks: Layer[] = [...PERMISSIONS, { id: "taint", name: "Taint" }];
    const chat = play(askC002, balanceCall("c1", "A001"), decided("c1", "allow", "cedar"));

    const strip = stripOf(chat, twoChecks);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.route).toEqual([
      { at: "you" },
      { at: "agent" },
      { at: "entrance" },
      { at: "check", index: 0 },
      { at: "check", index: 1 },
      { at: "server", server: "accounts" },
    ]);
    expect(strip.end).toBe("dot");
    expect(strip.checks).toEqual(["passed", "passed"]);
  });

  it("goes straight to the bank server when no checks are switched on", () => {
    const chat = play(askC002, balanceCall("c1", "A002"), decided("c1", "allow", "defences_off"));

    const strip = stripOf(chat, []);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.route.at(-1)).toEqual({ at: "server", server: "accounts" });
    expect(strip.checks).toEqual([]);
  });

  it("stops at the Gateway's door when the Gateway itself denies, before any check", () => {
    const chat = play(
      askC002,
      { type: "event", event: { event: "tool_call", data: { call_id: "c1", tool: "drop_tables", arguments: {} } } },
      decided("c1", "deny", "gateway", "No server has a tool called drop_tables."),
    );

    const strip = stripOf(chat, PERMISSIONS);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.route.at(-1)).toEqual({ at: "entrance" });
    expect(strip.end).toBe("cross");
    expect(strip.checks).toEqual(["idle"]);
    expect(strip.server).toBeNull();
  });

  it("never shows a ✕ for an allowed Tool call, even of a tool no known server owns", () => {
    const chat = play(
      askC002,
      { type: "event", event: { event: "tool_call", data: { call_id: "c1", tool: "new_tool", arguments: {} } } },
      decided("c1", "allow", "cedar"),
    );

    const strip = stripOf(chat, PERMISSIONS);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.end).toBe("dot");
    expect(strip.checks).toEqual(["passed"]);
    expect(strip.route.at(-1)).toEqual({ at: "check", index: 0 });
  });

  it("waits at the check that held the Tool call, never reaching the bank server", () => {
    const chat = play(askC002, balanceCall("c1", "A001"), decided("c1", "hold", "cedar"));

    const strip = stripOf(chat, PERMISSIONS);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.route.at(-1)).toEqual({ at: "check", index: 0 });
    expect(strip.end).toBe("wait");
    expect(strip.checks).toEqual(["held"]);
  });

  it("shows nothing before the first message", () => {
    expect(stripOf(emptyChat, PERMISSIONS)).toEqual({ status: "ready", route: [] });
  });

  it("shows the person's message going to the Agent before any Tool call", () => {
    const strip = stripOf(play(askC002), PERMISSIONS);

    expect(strip).toEqual({
      status: "asking",
      text: "Show me Rohan Mehta's balance (C002)",
      route: [{ at: "you" }, { at: "agent" }],
    });
  });

  it("holds the Tool call at the Gateway's door while the Gateway decides", () => {
    const strip = stripOf(play(askC002, balanceCall("c1", "A002")), PERMISSIONS);

    if (strip.status !== "checking") throw new Error(strip.status);
    expect(strip.route.at(-1)).toEqual({ at: "entrance" });
    expect(strip.checks).toEqual(["idle"]);
    expect(strip.step.callId).toBe("c1");
  });

  it("says the Tool call stopped when its turn ended without a Decision", () => {
    const chat = play(askC002, balanceCall("c1", "A002"), { type: "failed", message: "The Agent stopped." });

    expect(stripOf(chat, PERMISSIONS).status).toBe("stopped");
  });

  it("follows the newest Tool call across turns", () => {
    const chat = play(
      askC002,
      balanceCall("c1", "A002"),
      decided("c1", "deny", "cedar"),
      { type: "event", event: { event: "reply", data: { text: "No.", input_tokens: 1, output_tokens: 1, estimated_cost: null } } },
      { type: "ask", text: "What's my balance?" },
      balanceCall("c2", "A001"),
      decided("c2", "allow", "cedar"),
    );

    const strip = stripOf(chat, PERMISSIONS);

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.step.callId).toBe("c2");
  });

  it("shows an older Tool call the person opened in the chat instead of the newest", () => {
    const chat = play(
      askC002,
      balanceCall("c1", "A002"),
      decided("c1", "deny", "cedar"),
      balanceCall("c2", "A001"),
      decided("c2", "allow", "cedar"),
    );

    const strip = stripOf(chat, PERMISSIONS, "c1");

    if (strip.status !== "decided") throw new Error(strip.status);
    expect(strip.step.callId).toBe("c1");
    expect(strip.end).toBe("cross");
  });
});

describe("the one plain-English sentence under the strip", () => {
  const tell = (chat: ReturnType<typeof play>, layers: Layer[] = PERMISSIONS) => storyOf(stripOf(chat, layers), layers);

  it("says what was blocked, by which check, why, and that it never reached the bank server", () => {
    const chat = play(
      askC002,
      balanceCall("c1", "A002"),
      decided("c1", "deny", "cedar", "That account is not one this person may see."),
    );

    expect(tell(chat)).toEqual({
      head: "Blocked",
      tone: "deny",
      text:
        "The Agent tried to read the balance of account A002. The Permissions check stopped it. " +
        "That account is not one this person may see. It never reached the accounts server.",
    });
  });

  it("says which server an allowed Tool call reached", () => {
    const chat = play(askC002, balanceCall("c1", "A001"), decided("c1", "allow", "cedar"));

    expect(tell(chat)).toEqual({
      head: "Allowed",
      tone: "allow",
      text: "The Agent asked to read the balance of account A001. The Permissions check passed, so it went through to the accounts server.",
    });
  });

  it("says when no checks are switched on", () => {
    const chat = play(askC002, balanceCall("c1", "A002"), decided("c1", "allow", "defences_off"));

    expect(tell(chat, []).text).toBe(
      "The Agent asked to read the balance of account A002. No checks are switched on, so it went straight to the accounts server.",
    );
  });

  it("names the Gateway itself when it denies before any check", () => {
    const chat = play(
      askC002,
      { type: "event", event: { event: "tool_call", data: { call_id: "c1", tool: "drop_tables", arguments: {} } } },
      decided("c1", "deny", "gateway", "No server has a tool called drop_tables."),
    );

    expect(tell(chat).text).toBe("The Agent tried to use drop_tables. The Gateway stopped it. No server has a tool called drop_tables.");
  });

  it("tells the person what is happening before the Decision", () => {
    expect(tell(emptyChat)).toEqual({
      head: "Ready",
      tone: "transit",
      text: "Send the Agent a message. You will see each of its Tool calls travel through the Gateway here.",
    });
    expect(tell(play(askC002)).text).toBe(
      "You asked: “Show me Rohan Mehta's balance (C002)”. The Agent is working out which tool it needs…",
    );
    expect(tell(play(askC002, balanceCall("c1", "A002")))).toEqual({
      head: "Checking…",
      tone: "transit",
      text: "The Agent wants to read the balance of account A002. The Gateway is checking the Tool call…",
    });
  });
});
