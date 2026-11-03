import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Layer } from "./api";
import { type Chat, type Turn, totals } from "./chat";
import { DEMO_BANK, INTRO, type PersonId, ROLE_LABEL, ROLES, SUGGESTED } from "./people";
import { useSession } from "./session";
import { ToolCallStep } from "./ToolCallStep";

const count = new Intl.NumberFormat("en-IN");
const dollars = (cost: number) => `$${cost.toFixed(4)}`;

export function App() {
  const [person, setPerson] = useState<PersonId>("C001");
  // A new person is a new session and a new conversation.
  return <Workspace key={person} person={person} onSwitch={setPerson} />;
}

function Workspace({ person, onSwitch }: { person: PersonId; onSwitch: (p: PersonId) => void }) {
  const { session, chat, send } = useSession(person);
  const busy = session.status !== "ready" || chat.turns.some((t) => t.running);

  return (
    <div className="flex h-full flex-col">
      <TopBar person={person} onSwitch={onSwitch} chat={chat} />
      <main className="grid min-h-0 flex-1 grid-cols-[var(--spacing-chat)_1fr] max-[960px]:grid-cols-1 max-[960px]:grid-rows-[auto_auto]">
        <section
          aria-label="Chat with the Agent"
          className="flex min-h-0 flex-col border-r border-grid bg-paper-2 max-[960px]:min-h-[70vh] max-[960px]:border-r-0"
        >
          <header className="border-b border-grid px-5 pt-4 pb-3 max-sm:px-4">
            <h1 className="m-0 text-lg font-semibold tracking-[-0.01em]">
              {session.status === "ready" ? `${ROLE_LABEL[session.role]} ${session.person}` : "Logging in…"}
            </h1>
            <p className="m-0 mt-0.5 text-xs text-ink-3">
              {session.status === "ready" ? (
                <>
                  Conversation <code className="font-mono text-2xs">{session.conversationId.slice(0, 8)}</code> · the
                  Agent holds only your MCP token
                </>
              ) : session.status === "failed" ? (
                <span className="text-deny-ink">{session.message}</span>
              ) : (
                "Logging in at the Gateway"
              )}
            </p>
          </header>
          <ChatLog
            person={person}
            chat={chat}
            layers={session.status === "ready" ? session.layers : []}
            busy={busy}
            onAsk={(text) => send(text, true)}
          />
          {chat.turns.length ? (
            <div className="flex flex-wrap gap-1.5 px-4 pb-2.5">
              {SUGGESTED[person].map((s) => (
                <button
                  key={s.text}
                  type="button"
                  disabled={busy}
                  onClick={() => send(s.text, true)}
                  className="rounded-full border border-grid bg-sheet px-2.5 py-1 text-ui text-ink-2 hover:border-grid-strong hover:text-ink disabled:opacity-50"
                >
                  {s.text}
                </button>
              ))}
            </div>
          ) : null}
          <Composer busy={busy} onSend={(text) => send(text, false)} />
        </section>
        <GatewayPanel person={person} layers={session.status === "ready" ? session.layers : null} />
      </main>
    </div>
  );
}

function TopBar({ person, onSwitch, chat }: { person: PersonId; onSwitch: (p: PersonId) => void; chat: Chat }) {
  const { tokens, cost } = totals(chat);
  return (
    <header className="flex h-bar flex-none items-center gap-6 bg-rail px-5 text-rail-ink max-sm:h-auto max-sm:flex-wrap max-sm:gap-2.5 max-sm:px-4 max-sm:py-2.5">
      <div className="flex items-center gap-2.5 text-brand font-bold tracking-[-0.01em]">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <path d="M3 4 L8 11 L3 18" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          <line x1="11" y1="3" x2="11" y2="19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          <path d="M14 11 H19" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
        mcp-guard
      </div>
      <nav aria-label="Log in as" className="ml-auto flex gap-0.5 rounded-md bg-rail-2 p-[3px] max-sm:order-3 max-sm:ml-0 max-sm:w-full">
        {ROLES.map((r) => (
          <button
            key={r.id}
            type="button"
            aria-pressed={r.id === person}
            onClick={() => onSwitch(r.id)}
            className="rounded-sm px-3 py-1.5 text-sm font-medium whitespace-nowrap text-rail-ink-2 hover:text-rail-ink aria-pressed:bg-paper aria-pressed:font-semibold aria-pressed:text-rail max-sm:flex-1 max-sm:px-1 max-sm:text-xs"
          >
            {r.label}
            <span className="ml-1.5 font-mono text-xs opacity-75 max-sm:hidden">{r.id}</span>
          </button>
        ))}
      </nav>
      <div aria-live="polite" className="min-w-[170px] text-right text-xs text-rail-ink-2 max-sm:ml-auto max-sm:min-w-0">
        <b className="text-sm font-semibold text-rail-ink">{count.format(tokens)}</b> tokens
        {cost === null ? null : (
          <>
            {" "}· est. <b className="text-sm font-semibold text-rail-ink">{dollars(cost)}</b>
          </>
        )}
      </div>
    </header>
  );
}

function ChatLog(props: { person: PersonId; chat: Chat; layers: Layer[]; busy: boolean; onAsk: (text: string) => void }) {
  const { person, chat, layers, busy, onAsk } = props;
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [chat]);

  return (
    <div ref={log} className="flex flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 max-sm:px-4">
      {chat.turns.length === 0 ? (
        <div className="my-auto py-2">
          <h2 className="m-0 mb-2 text-xl font-semibold tracking-[-0.02em] text-balance">
            Chat with the Agent as {ROLES.find((r) => r.id === person)?.label} {person}
          </h2>
          <p className="m-0 mb-4.5 max-w-[42ch] text-ink-2">{INTRO[person]}</p>
          <div className="flex flex-col gap-2">
            {SUGGESTED[person].map((s) => (
              <button
                key={s.text}
                type="button"
                disabled={busy}
                onClick={() => onAsk(s.text)}
                className="flex items-center justify-between gap-2.5 rounded-lg border border-grid bg-sheet px-3 py-2.5 text-left text-base transition-colors duration-150 hover:border-grid-strong disabled:opacity-50"
              >
                <span>{s.text}</span>
                {s.gateway === "blocks" ? (
                  <em className="rounded-full bg-deny-tint px-2 py-0.5 text-2xs font-semibold whitespace-nowrap text-deny-ink not-italic">
                    Gateway blocks it
                  </em>
                ) : null}
              </button>
            ))}
          </div>
        </div>
      ) : (
        chat.turns.map((turn, i) => <TurnView key={i} turn={turn} layers={layers} />)
      )}
    </div>
  );
}

function TurnView({ turn, layers }: { turn: Turn; layers: Layer[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="max-w-[85%] self-end rounded-bubble rounded-br-xs bg-rail px-3 py-2 text-rail-ink">{turn.text}</div>
      {turn.steps.map((s) => (
        <ToolCallStep key={s.callId} step={s} layers={layers} />
      ))}
      {turn.running && turn.steps.every((s) => s.decision) ? (
        <div aria-label="The Agent is thinking" className="flex gap-1 self-start rounded-bubble rounded-bl-xs border border-grid bg-sheet px-3.5 py-3">
          {[0, 150, 300].map((delay) => (
            <i
              key={delay}
              className="size-1.5 rounded-full bg-ink-3 [animation:bob_1s_var(--ease-out)_infinite]"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
      ) : null}
      {turn.reply ? (
        <>
          <div className="max-w-[92%] self-start rounded-bubble rounded-bl-xs border border-grid bg-sheet px-3 py-2.5 whitespace-pre-wrap">
            {turn.reply.text}
          </div>
          <div className="pl-1 text-2xs text-ink-3">
            {count.format(turn.reply.tokens)} tokens
            {turn.reply.cost === null ? "" : ` · est. ${dollars(turn.reply.cost)}`} this turn
          </div>
        </>
      ) : null}
      {turn.error ? (
        <div role="alert" className="max-w-[92%] self-start rounded-lg bg-deny-tint px-3 py-2 text-sm text-deny-ink">
          {turn.error}
        </div>
      ) : null}
    </div>
  );
}

function Composer({ busy, onSend }: { busy: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!busy) input.current?.focus({ preventScroll: true });
  }, [busy]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    setText("");
    onSend(message);
  }

  return (
    <form onSubmit={submit} className="flex gap-2 border-t border-grid px-4 pt-3 pb-4">
      <input
        ref={input}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
        autoComplete="off"
        placeholder="Ask the Agent…"
        aria-label="Message to the Agent"
        className="min-w-0 flex-1 rounded-lg border border-grid-strong bg-sheet px-3 py-2.5 text-base text-ink caret-rail placeholder:text-ink-3 focus-visible:outline-offset-0 disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={busy || !text.trim()}
        className="flex items-center gap-1.5 rounded-lg bg-rail px-3.5 font-semibold text-rail-ink disabled:bg-grid-strong"
      >
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
          <path d="M1 7 H12 M8 3 L12 7 L8 11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Send
      </button>
    </form>
  );
}

function GatewayPanel({ person, layers }: { person: PersonId; layers: Layer[] | null }) {
  return (
    <section aria-label="The Gateway" className="graph-paper flex min-w-0 flex-col max-[960px]:order-first max-[960px]:border-b max-[960px]:border-grid">
      <div className="border-b border-grid bg-paper px-5 py-3 max-sm:px-4">
        <h2 className="m-0 text-lg font-semibold tracking-[-0.01em]">What the Gateway is doing</h2>
        <p className="m-0 text-xs text-ink-3">
          Every time the Agent wants to use a tool, the request has to pass the Gateway's checks first.
        </p>
        <p className="m-0 mt-2 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
          Checks switched on:
          {layers === null ? (
            <span className="text-ink-3">…</span>
          ) : layers.length === 0 ? (
            <span className="font-semibold text-deny-ink">none (defences are off)</span>
          ) : (
            layers.map((l) => (
              <span key={l.id} className="rounded-full bg-code px-2 py-0.5 font-semibold text-ink">
                {l.name}
              </span>
            ))
          )}
        </p>
      </div>
      <div className="p-5 max-sm:p-4">
        <DemoBank person={person} />
      </div>
    </section>
  );
}

function DemoBank({ person }: { person: PersonId }) {
  return (
    <aside aria-labelledby="demo-bank" className="max-w-[360px] rounded-md border border-grid bg-sheet px-4 py-3 shadow-float">
      <h3 id="demo-bank" className="m-0 text-base font-bold">
        Demo bank
      </h3>
      <p className="m-0 mb-2 text-xs text-ink-3">All its people and money are fake.</p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {DEMO_BANK.map((p) => (
          <li key={p.id} className="flex items-baseline gap-2 text-sm">
            <span className="w-11 flex-none font-mono text-xs text-ink-3">{p.id}</span>
            <span className="min-w-0 flex-1">
              <span className="font-semibold">{p.name}</span>
              <span className="text-ink-2"> · {p.what}</span>
            </span>
            {p.id === person ? (
              <span className="rounded-full bg-rail px-2 py-px text-2xs font-semibold text-rail-ink">You</span>
            ) : null}
          </li>
        ))}
      </ul>
    </aside>
  );
}
