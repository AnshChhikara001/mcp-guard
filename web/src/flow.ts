import type { Layer } from "./api";
import type { Chat, Decision, ToolCallStep, Turn } from "./chat";
import { askSentence } from "./sentences";

/**
 * The flow strip: the newest Tool call of the person's own chat, drawn as a trip from You
 * through the Agent and the Gateway's checks to a bank server, told in one plain sentence.
 */

/** The fake bank's MCP servers, top to bottom on the strip. */
export const SERVERS = ["accounts", "transactions", "payments", "tickets", "notify", "fx-rates"] as const;
export type Server = (typeof SERVERS)[number];

// Copied from the fake bank's servers (fake_servers/src/mcp_guard/fake_servers/*.py, one file
// per server); a new tool there needs a line here, or the strip shows no server for it.
const SERVER_OF: Record<string, Server> = {
  get_customer_profile: "accounts",
  list_accounts: "accounts",
  get_balance: "accounts",
  list_transactions: "transactions",
  raise_dispute: "transactions",
  refund: "payments",
  list_tickets: "tickets",
  get_ticket: "tickets",
  send_email: "notify",
  send_sms: "notify",
  convert: "fx-rates",
};

/** The bank server that owns `tool`, or null when no bank server offers it. */
export function serverOf(tool: string): Server | null {
  return Object.hasOwn(SERVER_OF, tool) ? (SERVER_OF[tool] ?? null) : null;
}

/** A place the Tool call's dot stops on its way. */
export type Stop =
  | { at: "you" }
  | { at: "agent" }
  | { at: "entrance" } // the Gateway's door, before its first check
  | { at: "check"; index: number } // the index-th check GET /layers listed
  | { at: "server"; server: Server };

/** What a check did to the Tool call: not reached yet, let it through, stopped it, or held it. */
export type CheckState = "idle" | "passed" | "blocked" | "held";

type OnTheWay = {
  step: ToolCallStep;
  server: Server | null;
  route: Stop[]; // where the dot goes, in order; it ends at the last stop
  checks: CheckState[]; // one per check, in the order GET /layers listed them
};

export type Strip =
  | { status: "ready"; route: [] } // nothing asked yet
  | { status: "asking"; text: string; route: Stop[] } // the message is with the Agent; no Tool call yet
  | ({ status: "checking" } & OnTheWay) // at the Gateway's door, waiting for its Decision
  | ({ status: "stopped" } & OnTheWay) // the turn ended before a Decision arrived
  | ({
      status: "decided";
      decision: Decision;
      // a red ✕ where it was stopped, an amber wait at the check that held it, or a dot at the
      // bank server
      end: "cross" | "wait" | "dot";
    } & OnTheWay);

const TO_THE_AGENT: Stop[] = [{ at: "you" }, { at: "agent" }];
const TO_THE_DOOR: Stop[] = [...TO_THE_AGENT, { at: "entrance" }];

/**
 * The strip for one Tool call of `chat`, through the checks `layers` lists: the one `opened`
 * names (opened in the chat), or else the newest, live.
 */
export function stripOf(chat: Chat, layers: Layer[], opened: string | null = null): Strip {
  const found = opened === null ? undefined : openedStep(chat, opened);
  const turn = chat.turns.at(-1);
  if (!found && turn?.running && turn.steps.length === 0)
    return { status: "asking", text: turn.text, route: TO_THE_AGENT };
  const owner = found?.owner ?? chat.turns.findLast((t) => t.steps.length > 0);
  const step = found?.step ?? owner?.steps.at(-1);
  if (!owner || !step) return { status: "ready", route: [] };

  const server = serverOf(step.tool);
  if (!step.decision) {
    const waiting = { step, server, route: TO_THE_DOOR, checks: layers.map((): CheckState => "idle") };
    return { status: owner.running ? "checking" : "stopped", ...waiting };
  }

  const { decision, layer } = step.decision;
  const passes = decision === "allow" || decision === "redact";
  // A deny or Hold stops at its check; one by the Gateway itself (not a listed check) stops at
  // the door.
  const last = passes ? layers.length - 1 : layers.findIndex((l) => l.id === layer);
  const route: Stop[] = [...TO_THE_DOOR];
  for (let index = 0; index <= last; index++) route.push({ at: "check", index });
  if (passes && server) route.push({ at: "server", server });
  const stopped: CheckState = decision === "hold" ? "held" : "blocked";
  const checks = layers.map((_, i): CheckState => (passes || i < last ? "passed" : i === last ? stopped : "idle"));
  const end = passes ? "dot" : decision === "hold" && last >= 0 ? "wait" : "cross";
  return { status: "decided", step, server, decision, route, end, checks };
}

function openedStep(chat: Chat, callId: string): { owner: Turn; step: ToolCallStep } | undefined {
  for (const owner of chat.turns) {
    const step = owner.steps.find((s) => s.callId === callId);
    if (step) return { owner, step };
  }
  return undefined;
}

/** The name GET /layers gives the check `layerId`; null for the Gateway's own layer, which is no check. */
export function checkName(layers: Layer[], layerId: string | undefined): string | null {
  return layers.find((l) => l.id === layerId)?.name ?? null;
}

/** The big word over the sentence, and the Decision colour it takes ("transit" before one). */
export type Story = { head: string; tone: Decision | "transit"; text: string };

/** The strip told in one or two plain sentences, with the names `layers` gives the checks. */
export function storyOf(strip: Strip, layers: Layer[]): Story {
  switch (strip.status) {
    case "ready":
      return {
        head: "Ready",
        tone: "transit",
        text: "Send the Agent a message. You will see each of its Tool calls travel through the Gateway here.",
      };
    case "asking":
      return {
        head: "Asking the Agent",
        tone: "transit",
        text: `You asked: “${strip.text}”. The Agent is working out which tool it needs…`,
      };
  }
  const verb = askSentence(strip.step.tool, strip.step.arguments);
  const reached = strip.server ? ` the ${strip.server} server` : " a bank server";
  if (strip.status === "checking")
    return { head: "Checking…", tone: "transit", text: `The Agent wants to ${verb}. The Gateway is checking the Tool call…` };
  if (strip.status === "stopped")
    return {
      head: "Stopped",
      tone: "transit",
      text: `The Agent wanted to ${verb}, but the turn ended before the Gateway's Decision arrived.`,
    };

  const decided = strip.step.decision;
  const name = checkName(layers, decided?.layer);
  const who = name ? `The ${name} check` : "The Gateway"; // the Gateway's own denies name no check
  // The Gateway's explanation is a sentence of its own: "That account is not one …"
  const explanation = decided?.explanation?.trim();
  const because = explanation ? `. ${/[.!?]$/.test(explanation) ? explanation : `${explanation}.`}` : ".";
  switch (strip.decision) {
    case "deny":
      return {
        head: "Blocked",
        tone: "deny",
        text: `The Agent tried to ${verb}. ${who} stopped it${because}${strip.server ? ` It never reached${reached}.` : ""}`,
      };
    case "hold":
      return { head: "Waiting for a Supervisor", tone: "hold", text: `The Agent tried to ${verb}. ${who} paused it${because}` };
    case "redact":
      return {
        head: "Personal data hidden",
        tone: "redact",
        text: `The Agent asked to ${verb}. ${who} hid personal data before the model saw it, then it went through to${reached}.`,
      };
    case "allow": {
      const passed =
        layers.length === 0
          ? "No checks are switched on, so it went straight to"
          : layers.length === 1
            ? `The ${layers[0]?.name} check passed, so it went through to`
            : "Every check passed, so it went through to";
      return { head: "Allowed", tone: "allow", text: `The Agent asked to ${verb}. ${passed}${reached}.` };
    }
  }
}
