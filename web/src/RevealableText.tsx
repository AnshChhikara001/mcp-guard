import { useState } from "react";
import type { Revealed } from "./api";
import { splitPseudonyms } from "./pseudonyms";

type OnReveal = (pseudonym: string) => Promise<Revealed>;

/** A reply in which each Pseudonym is a button: pressed, the Gateway shows the real value, or
 * says why it stays hidden. Nothing is revealed until the person asks. */
export function RevealableText({ text, onReveal }: { text: string; onReveal: OnReveal }) {
  return splitPseudonyms(text).map((piece, i) =>
    "text" in piece ? piece.text : <Pseudonym key={i} pseudonym={piece.pseudonym} onReveal={onReveal} />,
  );
}

type State = { status: "hidden" } | { status: "asking" } | ({ status: "answered" } & Revealed);

function Pseudonym({ pseudonym, onReveal }: { pseudonym: string; onReveal: OnReveal }) {
  const [state, setState] = useState<State>({ status: "hidden" });
  if (state.status === "answered" && "value" in state) {
    return (
      <mark title={`Revealed from ${pseudonym}`} className="rounded-xs bg-allow-tint px-1 text-allow-ink">
        {state.value}
      </mark>
    );
  }
  const refusal = state.status === "answered" ? state.hidden : null;
  return (
    <button
      type="button"
      aria-label={`Reveal ${pseudonym}`}
      disabled={state.status !== "hidden"}
      title={refusal ?? "Reveal the real value (the Gateway checks you may see it)"}
      onClick={async () => {
        setState({ status: "asking" });
        setState({ status: "answered", ...(await onReveal(pseudonym)) });
      }}
      className={
        refusal === null
          ? "rounded-xs bg-redact-tint px-1 font-mono text-[0.9em] text-redact-ink underline decoration-dotted hover:decoration-solid"
          : "rounded-xs bg-deny-tint px-1 font-mono text-[0.9em] text-deny-ink line-through"
      }
    >
      {pseudonym}
    </button>
  );
}
