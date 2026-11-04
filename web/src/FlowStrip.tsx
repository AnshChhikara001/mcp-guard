import { useEffect, useState } from "react";
import type { Layer } from "./api";
import { type CheckState, checkName, SERVERS, type Server, type Stop, type Strip, storyOf } from "./flow";

/* Geometry, from the agreed prototype: a 1000 × 200 drawing that scales with the panel. */
const YOU = 70;
const AGENT = 215;
const GATE_LEFT = 300;
const GATE_RIGHT = 780;
const SERVER_DOT = 872;
const RAIL = 96; // the line You → Agent → Gateway runs along
const HOP_MS = 320; // the dot's time from one stop to the next

const checkX = (index: number, count: number) => GATE_LEFT + ((GATE_RIGHT - GATE_LEFT) * (index + 0.5)) / count;
const serverY = (server: Server) => 26 + SERVERS.indexOf(server) * 29;

function pointOf(stop: Stop, checks: number): [number, number] {
  switch (stop.at) {
    case "you":
      return [YOU, RAIL];
    case "agent":
      return [AGENT, RAIL];
    case "entrance":
      return [GATE_LEFT, RAIL];
    case "check":
      return [checkX(stop.index, checks), RAIL];
    case "server":
      return [SERVER_DOT, serverY(stop.server)];
  }
}

const TONE_COLOR = {
  allow: "var(--color-allow)",
  redact: "var(--color-redact)",
  hold: "var(--color-hold)",
  deny: "var(--color-deny)",
  transit: "var(--color-transit)",
};
const TONE_INK = {
  allow: "text-allow-ink",
  redact: "text-redact-ink",
  hold: "text-hold-ink",
  deny: "text-deny",
  transit: "text-ink-2",
};
const CHECK_COLOR: Record<CheckState, string> = {
  idle: "var(--color-idle)",
  passed: "var(--color-allow)",
  blocked: "var(--color-deny)",
  held: "var(--color-hold)",
};
const CHECK_WORD: Record<CheckState, string> = { idle: "", passed: "passed", blocked: "blocked", held: "waiting" };

const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * How far along its route the dot is, in stops (1.5 = halfway from the second stop to the
 * third). It moves on as the route grows, and starts again from You when `replayKey` changes.
 */
function useProgress(replayKey: string, stops: number): number {
  const [state, setState] = useState({ replayKey, at: 0 });
  const at = state.replayKey === replayKey ? state.at : 0;
  const target = Math.max(0, stops - 1);

  useEffect(() => {
    if (reducedMotion()) {
      setState({ replayKey, at: target });
      return;
    }
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const step = (now - last) / HOP_MS;
      last = now;
      let done = false;
      setState((s) => {
        const from = s.replayKey === replayKey ? s.at : 0;
        const next = Math.min(target, from + step);
        done = next >= target;
        return { replayKey, at: next };
      });
      if (!done) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [replayKey, target]);

  return Math.min(at, target);
}

function dotAt(points: [number, number][], at: number): [number, number] {
  const i = Math.floor(at);
  const a = points[i] ?? points[0] ?? [YOU, RAIL];
  const b = points[i + 1] ?? a;
  const f = at - i;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

type Props = {
  strip: Strip;
  layers: Layer[];
  replayKey: string; // changes when the strip should play from the start
  you: string; // who is chatting, under "You": "Customer C001"
  opened: boolean; // showing a Tool call opened in the chat, not the live one
  onReplay: () => void;
  onBackToLive: () => void;
};

/** You → Agent → the Gateway's checks → the bank servers, with the Tool call's dot travelling along. */
export function FlowStrip({ strip, layers, replayKey, you, opened, onReplay, onBackToLive }: Props) {
  const route = strip.route;
  const at = useProgress(replayKey, route.length);
  const points = route.map((s) => pointOf(s, layers.length));
  const arrived = route.length > 0 && at >= route.length - 1;
  const decided = strip.status === "decided" && arrived ? strip : null;
  // Until the dot gets there, the sentence says the Gateway is still checking.
  const story = storyOf(strip.status === "decided" && !arrived ? { ...strip, status: "checking" } : strip, layers);
  const stoppedBy = decided ? (checkName(layers, decided.step.decision?.layer) ?? "the Gateway") : "";
  const color = decided ? TONE_COLOR[story.tone] : TONE_COLOR.transit;
  const dot = points.length ? dotAt(points, at) : null;
  const travelled = dot ? [...points.slice(0, Math.floor(at) + 1), dot] : [];
  const routeIndexOfCheck = (i: number) => route.findIndex((s) => s.at === "check" && s.index === i);
  const target = "server" in strip ? strip.server : null;
  const end = decided ? decided.end : null;
  const thinking = strip.status === "asking" && arrived;

  return (
    <section
      aria-label="The Tool call's trip through the Gateway"
      className="@container border-b border-grid bg-paper-2"
    >
      {/* The sentence sits beside the drawing only when the panel is wide enough for both at full size. */}
      <div className="grid grid-cols-1 items-center gap-x-7 gap-y-2 px-6 pt-3.5 pb-4 max-sm:px-4 @min-[1100px]:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 overflow-x-auto">
          <svg
            viewBox="0 0 1000 200"
            role="img"
            aria-label="The Tool call travelling from you, through the Agent and the Gateway's checks, to a bank server"
            className="block h-auto max-h-[230px] w-full min-w-[620px]"
          >
            {/* rails */}
            <line x1={YOU} y1={RAIL} x2={GATE_RIGHT + 30} y2={RAIL} stroke="var(--color-grid)" strokeWidth="4" strokeLinecap="round" />
            {SERVERS.map((s) => (
              <path key={s} d={`M${GATE_RIGHT + 30} ${RAIL} L${SERVER_DOT} ${serverY(s)}`} stroke="var(--color-grid-faint)" strokeWidth="2" fill="none" />
            ))}

            {/* the Gateway and its checks */}
            <rect x={GATE_LEFT} y="12" width={GATE_RIGHT - GATE_LEFT} height="172" rx="14" fill="var(--color-rail)" fillOpacity="0.045" stroke="var(--color-rail)" strokeWidth="2" />
            <text x={GATE_LEFT + 16} y="36" fontSize="16" fontWeight="750" fill="var(--color-rail)">
              mcp-guard Gateway
            </text>
            <text x={GATE_RIGHT - 16} y="36" textAnchor="end" fontSize="12.5" fill="var(--color-ink-3)">
              {layers.length === 0 ? "no checks switched on" : layers.length === 1 ? "1 check" : `${layers.length} checks, in order`}
            </text>
            {layers.map((l, i) => {
              const x = checkX(i, layers.length);
              const reached = strip.status === "decided" && at >= routeIndexOfCheck(i) && routeIndexOfCheck(i) >= 0;
              const state: CheckState = reached ? (strip.checks[i] ?? "idle") : "idle";
              return (
                <g key={l.id}>
                  <rect x={x - 6} y={RAIL - 32} width="12" height="64" rx="4" fill={CHECK_COLOR[state]} />
                  <text x={x} y={RAIL + 54} textAnchor="middle" fontSize="13" fontWeight="650" fill="var(--color-ink)" letterSpacing="0.04em" style={{ fontStretch: "80%" }}>
                    {l.name.toUpperCase()}
                  </text>
                  {state === "idle" ? null : (
                    <text x={x} y={RAIL + 71} textAnchor="middle" fontSize="12" fontWeight="600" fill={state === "held" ? "var(--color-hold-ink)" : CHECK_COLOR[state]}>
                      {CHECK_WORD[state]}
                    </text>
                  )}
                </g>
              );
            })}

            {/* the path so far, and the dot */}
            {travelled.length > 1 ? (
              <polyline points={travelled.map((p) => p.join(",")).join(" ")} fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
            ) : null}

            {/* You */}
            <circle cx={YOU} cy={RAIL} r="28" fill="var(--color-sheet)" stroke="var(--color-ink)" strokeWidth="2" />
            <circle cx={YOU} cy={RAIL - 8} r="8" fill="var(--color-ink)" />
            <path d={`M${YOU - 14} ${RAIL + 17} a14 12 0 0 1 28 0`} fill="var(--color-ink)" />
            <text x={YOU} y={RAIL + 50} textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--color-ink)">
              You
            </text>
            <text x={YOU} y={RAIL + 67} textAnchor="middle" fontSize="12" fill="var(--color-ink-3)">
              {you}
            </text>

            {/* the Agent */}
            {thinking ? (
              <rect x={AGENT - 33} y={RAIL - 33} width="66" height="66" rx="17" fill="none" stroke="var(--color-rail)" strokeOpacity="0.35" strokeWidth="2" className="[animation:bob_1s_var(--ease-out)_infinite]" />
            ) : null}
            <rect x={AGENT - 28} y={RAIL - 28} width="56" height="56" rx="14" fill="var(--color-rail)" />
            <path
              d={`M${AGENT} ${RAIL - 14} C${AGENT + 2} ${RAIL - 3} ${AGENT + 3} ${RAIL - 2} ${AGENT + 14} ${RAIL} C${AGENT + 3} ${RAIL + 2} ${AGENT + 2} ${RAIL + 3} ${AGENT} ${RAIL + 14} C${AGENT - 2} ${RAIL + 3} ${AGENT - 3} ${RAIL + 2} ${AGENT - 14} ${RAIL} C${AGENT - 3} ${RAIL - 2} ${AGENT - 2} ${RAIL - 3} ${AGENT} ${RAIL - 14}Z`}
              fill="var(--color-rail-ink)"
            />
            <text x={AGENT} y={RAIL + 50} textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--color-ink)">
              Agent
            </text>
            <text x={AGENT} y={RAIL + 67} textAnchor="middle" fontSize="12" fill="var(--color-ink-3)">
              LLM + tools
            </text>

            {/* the bank servers */}
            <text x={SERVER_DOT} y="12" fontSize="11" fontWeight="700" fill="var(--color-ink-3)" letterSpacing="0.12em">
              BANK SERVERS
            </text>
            {SERVERS.map((s) => {
              const aimed = s === target;
              const reached = aimed && end === "dot";
              const mark = reached ? color : aimed ? "var(--color-ink)" : "var(--color-grid-strong)";
              return (
                <g key={s} {...(reached ? { role: "img", "aria-label": `Reached the ${s} server` } : {})}>
                  <circle cx={SERVER_DOT} cy={serverY(s)} r={reached ? 6 : 4} fill={reached ? mark : "var(--color-sheet)"} stroke={mark} strokeWidth="2" />
                  <text x={SERVER_DOT + 14} y={serverY(s) + 4.5} fontSize="13.5" fontWeight={aimed ? 700 : 450} fill={aimed ? "var(--color-ink)" : "var(--color-ink-3)"}>
                    {s}
                  </text>
                </g>
              );
            })}

            {dot ? (
              <circle cx={dot[0]} cy={dot[1]} r="10" fill={strip.status === "stopped" && arrived ? "var(--color-ink-3)" : color} stroke="var(--color-paper-2)" strokeWidth="3" />
            ) : null}
            {end === "cross" && dot ? (
              <g role="img" aria-label={`Blocked by ${stoppedBy}`}>
                <path
                  d={`M${dot[0] + 9} ${dot[1] - 9} l18 18 M${dot[0] + 27} ${dot[1] - 9} l-18 18`}
                  stroke="var(--color-deny)"
                  strokeWidth="4"
                  strokeLinecap="round"
                />
              </g>
            ) : null}
            {end === "wait" && dot ? (
              <circle cx={dot[0]} cy={dot[1]} r="15" fill="none" stroke="var(--color-hold)" strokeWidth="2.5" className="[animation:bob_1.6s_var(--ease-out)_infinite]" />
            ) : null}
          </svg>
        </div>

        <div aria-live="polite">
          <span className={`inline-block text-xl font-bold tracking-[-0.02em] ${TONE_INK[story.tone]}`}>{story.head}</span>
          <p className="m-0 mt-1.5 mb-2.5 max-w-[60ch] text-md text-pretty text-ink">{story.text}</p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={route.length === 0}
              onClick={onReplay}
              className="inline-flex items-center gap-1.5 rounded-md border border-grid-strong bg-sheet px-2.75 py-1.25 text-ui font-semibold text-ink-2 enabled:hover:border-ink-3 enabled:hover:text-ink disabled:opacity-45"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M2 6 a4 4 0 1 0 1.2-2.9 M2 1.5 V3.6 H4.1" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              Replay
            </button>
            {opened ? (
              <button
                type="button"
                onClick={onBackToLive}
                className="inline-flex items-center rounded-md border border-grid-strong bg-sheet px-2.75 py-1.25 text-ui font-semibold text-ink-2 hover:border-ink-3 hover:text-ink"
              >
                Back to live
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
