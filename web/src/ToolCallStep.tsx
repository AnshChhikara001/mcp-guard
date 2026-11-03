import { useId, useState } from "react";
import type { Layer } from "./api";
import type { Decision, ToolCallStep as Step } from "./chat";
import { askSentence } from "./sentences";

const VERDICT: Record<Decision, { word: string; ink: string; mark: string }> = {
  allow: { word: "Allowed", ink: "text-allow-ink", mark: "var(--color-allow)" },
  redact: { word: "Redacted by", ink: "text-redact-ink", mark: "var(--color-redact)" },
  hold: { word: "Held by", ink: "text-hold-ink", mark: "var(--color-hold)" },
  deny: { word: "Blocked by", ink: "text-deny-ink", mark: "var(--color-deny)" },
};

const UNKNOWN = { word: "Decided by", ink: "text-ink", mark: "var(--color-ink-3)" };
const verdictOf = (decision: string) => VERDICT[decision as Decision] ?? UNKNOWN;

/** The small line mark the flow strip also uses: dashed in transit, ✕ when denied. */
function Tick({ decision }: { decision: Decision | null }) {
  const common = { className: "h-2.5 w-[18px] flex-none", viewBox: "0 0 18 10", "aria-hidden": true };
  if (decision === null)
    return (
      <svg {...common}>
        <line x1="0" y1="5" x2="18" y2="5" stroke="var(--color-ink-3)" strokeWidth="2" strokeDasharray="3 3" />
      </svg>
    );
  const color = verdictOf(decision).mark;
  if (decision === "deny")
    return (
      <svg {...common}>
        <line x1="0" y1="5" x2="10" y2="5" stroke={color} strokeWidth="2.4" />
        <path d="M11 1.5 L17 8.5 M17 1.5 L11 8.5" stroke={color} strokeWidth="2" />
      </svg>
    );
  if (decision === "hold")
    return (
      <svg {...common}>
        <line x1="0" y1="5" x2="18" y2="5" stroke={color} strokeWidth="4" />
      </svg>
    );
  return (
    <svg {...common}>
      <line x1="0" y1="5" x2="12" y2="5" stroke={color} strokeWidth="2.4" />
      <circle cx="14" cy="5" r="3.2" fill={color} />
    </svg>
  );
}

/** One Tool call of a turn: what the Agent asked, then the Gateway's Decision. Click for details. */
export function ToolCallStep({ step, layers }: { step: Step; layers: Layer[] }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const decided = step.decision;
  const layerName = decided ? (layers.find((l) => l.id === decided.layer)?.name ?? decided.layer) : "";
  const verdict = decided ? verdictOf(decided.decision) : null;

  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-2 rounded-sm px-1.5 py-1 text-left text-sm text-ink-2 hover:bg-hover aria-expanded:bg-hover"
      >
        <span className="mt-[5px]">
          <Tick decision={decided?.decision ?? null} />
        </span>
        <span className="min-w-0 flex-1">
          The Agent asks to {askSentence(step.tool, step.arguments)}
          {verdict ? (
            <>
              {". "}
              <b className={`font-semibold ${verdict.ink}`}>
                {verdict.word}
                {decided?.decision === "allow" ? "" : ` ${layerName}`}.
              </b>
              {decided?.decision !== "allow" && decided?.explanation ? (
                <span className="mt-0.5 block text-ink-2">{decided.explanation}</span>
              ) : null}
            </>
          ) : (
            <span className="dots text-ink-3">
              {" "}
              · the Gateway is checking
            </span>
          )}
        </span>
      </button>
      {open ? (
        <dl
          id={detailsId}
          className="mt-1 mb-1 ml-8 grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-1 rounded-md border border-grid bg-sheet px-3 py-2.5 text-xs"
        >
          <dt className="text-ink-3">Tool</dt>
          <dd className="m-0 font-mono">{step.tool}</dd>
          <dt className="text-ink-3">Arguments</dt>
          <dd className="m-0 font-mono break-words whitespace-pre-wrap">
            {JSON.stringify(step.arguments, null, 2)}
          </dd>
          {decided ? (
            <>
              <dt className="text-ink-3">Decision</dt>
              <dd className={`m-0 font-mono ${verdict?.ink}`}>{decided.decision}</dd>
              {decided.reason ? (
                <>
                  <dt className="text-ink-3">Reason</dt>
                  <dd className="m-0 font-mono">{decided.reason}</dd>
                </>
              ) : null}
              <dt className="text-ink-3">Check</dt>
              <dd className="m-0 font-mono">
                {layerName} <span className="text-ink-3">({decided.layer})</span>
              </dd>
              <dt className="text-ink-3">Audit entry</dt>
              <dd className="m-0 font-mono">{decided.auditId === null ? "none" : `#${decided.auditId}`}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
    </div>
  );
}
