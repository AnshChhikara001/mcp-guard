/** The chat as the browser shows it, built one step at a time from the Agent API's events. */

/** One event of a turn's stream (see agent/src/mcp_guard/agent/api.py). */
export type ChatEvent =
  | {
      event: "tool_call";
      data: { call_id: string; tool: string; arguments: Record<string, unknown> };
    }
  | {
      event: "decision";
      data: {
        call_id: string;
        decision: Decision;
        reason: string | null;
        explanation: string | null;
        layer: string;
        audit_id: number | null;
      };
    }
  | {
      event: "reply";
      data: {
        text: string;
        input_tokens: number;
        output_tokens: number;
        estimated_cost: number | null;
      };
    }
  | { event: "error"; data: { message: string } };

export type Decision = "allow" | "redact" | "hold" | "deny";

export type ToolCallStep = {
  callId: string;
  tool: string;
  arguments: Record<string, unknown>;
  decision: {
    decision: Decision;
    reason: string | null;
    explanation: string | null;
    layer: string;
    auditId: number | null;
  } | null; // null while the Gateway hasn't decided yet
};

export type Turn = {
  text: string;
  steps: ToolCallStep[];
  reply: { text: string; tokens: number; cost: number | null } | null;
  error: string | null;
  running: boolean;
};

export type Chat = { turns: Turn[] };

export type Action =
  | { type: "ask"; text: string } // the person sends a message: a new turn starts
  | { type: "event"; event: ChatEvent } // the running turn's next event
  | { type: "failed"; message: string }; // the turn's request failed or its stream broke off

export const emptyChat: Chat = { turns: [] };

export function step(chat: Chat, action: Action): Chat {
  if (action.type === "ask") {
    const turn: Turn = { text: action.text, steps: [], reply: null, error: null, running: true };
    return { turns: [...chat.turns, turn] };
  }
  const last = chat.turns.at(-1);
  if (!last?.running) return chat;
  return { turns: [...chat.turns.slice(0, -1), applyTo(last, action)] };
}

function applyTo(turn: Turn, action: Exclude<Action, { type: "ask" }>): Turn {
  if (action.type === "failed") return { ...turn, error: action.message, running: false };
  const { event } = action;
  switch (event.event) {
    case "tool_call": {
      const { call_id, tool, arguments: args } = event.data;
      return {
        ...turn,
        steps: [...turn.steps, { callId: call_id, tool, arguments: args, decision: null }],
      };
    }
    case "decision": {
      const { call_id, audit_id, ...rest } = event.data;
      return {
        ...turn,
        steps: turn.steps.map((s) =>
          s.callId === call_id ? { ...s, decision: { ...rest, auditId: audit_id } } : s,
        ),
      };
    }
    case "reply": {
      const { text, input_tokens, output_tokens, estimated_cost } = event.data;
      const reply = { text, tokens: input_tokens + output_tokens, cost: estimated_cost };
      return { ...turn, reply, running: false };
    }
    case "error":
      return { ...turn, error: event.data.message, running: false };
    default:
      return turn; // an event this app doesn't know yet
  }
}

/**
 * Tokens and estimated cost over the conversation's finished turns. The cost is null if any
 * turn had no price, so a partial sum never passes for the whole.
 */
export function totals(chat: Chat): { tokens: number; cost: number | null } {
  const replies = chat.turns.flatMap((t) => (t.reply ? [t.reply] : []));
  const tokens = replies.reduce((sum, r) => sum + r.tokens, 0);
  let cost: number | null = replies.length ? 0 : null;
  for (const r of replies) cost = cost === null || r.cost === null ? null : cost + r.cost;
  return { tokens, cost };
}
