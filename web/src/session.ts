/**
 * One person's session: logged in at the Gateway, with a fresh conversation at the Agent.
 * When a token expires, it logs in again silently and the same conversation carries on
 * (a conversation belongs to the person, not to one token).
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  type Layer,
  type Login,
  Refused,
  type Role,
  Unauthorized,
  layers,
  login,
  me,
  openConversation,
  takeTurn,
} from "./api";
import { type ChatEvent, emptyChat, step } from "./chat";
import { readEvents } from "./sse";

export type Session =
  | { status: "starting" }
  | { status: "failed"; message: string }
  | {
      status: "ready";
      person: string;
      role: Role;
      conversationId: string;
      layers: Layer[];
    };

export function useSession(personId: string) {
  const [session, setSession] = useState<Session>({ status: "starting" });
  const [chat, dispatch] = useReducer(step, emptyChat);
  const tokens = useRef<Login | null>(null);
  const leaving = useRef(new AbortController());

  useEffect(() => {
    let current = true;
    const left = new AbortController();
    leaving.current = left;
    (async () => {
      const logged = await login(personId);
      if (!current) return;
      tokens.current = logged;
      const [who, conversationId, checks] = await Promise.all([
        me(logged.uiToken),
        openConversation(logged.mcpToken),
        layers(),
      ]);
      if (current) setSession({ status: "ready", ...who, conversationId, layers: checks });
    })().catch((e: unknown) => {
      if (current) setSession({ status: "failed", message: problem(e) });
    });
    return () => {
      current = false;
      left.abort(); // stop listening to a running turn; the turn itself finishes at the Agent
    };
  }, [personId]);

  /** Runs `call` with the MCP token; if the token was refused, logs in again and retries once. */
  const withMcpToken = useCallback(
    async <T,>(call: (mcpToken: string) => Promise<T>): Promise<T> => {
      if (!tokens.current) throw new Error("Not logged in yet.");
      try {
        return await call(tokens.current.mcpToken);
      } catch (e) {
        if (!(e instanceof Unauthorized)) throw e;
        tokens.current = await login(personId);
        return await call(tokens.current.mcpToken);
      }
    },
    [personId],
  );

  const send = useCallback(
    async (text: string, suggested: boolean) => {
      if (session.status !== "ready") return;
      dispatch({ type: "ask", text });
      try {
        const body = await withMcpToken((token) =>
          takeTurn(
            token,
            session.conversationId,
            suggested ? { text, suggested } : { text },
            leaving.current.signal,
          ),
        );
        let ended = false;
        for await (const event of readEvents(body)) {
          dispatch({ type: "event", event: event as ChatEvent });
          ended ||= event.event === "reply" || event.event === "error";
        }
        if (!ended) dispatch({ type: "failed", message: "The Agent stopped before replying." });
      } catch (e) {
        if (leaving.current.signal.aborted) return;
        dispatch({ type: "failed", message: problem(e) });
      }
    },
    [session, withMcpToken],
  );

  return { session, chat, send };
}

function problem(e: unknown): string {
  if (e instanceof Refused) return e.message;
  if (e instanceof Unauthorized) return "The Gateway refused the login.";
  return "Couldn't reach the Agent or the Gateway. Please try again.";
}
