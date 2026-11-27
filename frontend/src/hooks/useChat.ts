"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, clearSession, sendFeedback, streamChat, type ApiConfig } from "@/lib/api";
import type { ChatMessage, FeedbackReason, Rating } from "@/lib/types";

export const MAX_CHARS = 1000; // mirrors the server limit

/** Instant (0-token) answers wait this long, so the typing dots show first and the bot feels like it is thinking. Model answers already take longer. */
export const MIN_THINK_MS = Number(process.env.NEXT_PUBLIC_MIN_THINK_MS ?? 600);

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, ms);
    const onAbort = () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

const uid = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

interface Options {
  /** Persist the conversation in sessionStorage under this key (e.g. derived from the user id). Omit to keep it in memory only. */
  storageKey?: string;
  onAuthError?: () => void;
  /** the content page the user is looking at: the assistant answers "this lesson" questions about it */
  pageId?: string | null;
}

function load<T>(key: string | undefined, suffix: string, fallback: T): T {
  if (!key || typeof window === "undefined") return fallback;
  try {
    const raw = window.sessionStorage.getItem(`grovator.chat.${key}.${suffix}`);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function useChat(cfg: ApiConfig, opts: Options = {}) {
  const { storageKey, onAuthError, pageId } = opts;
  const pageRef = useRef<string | null>(pageId ?? null);
  pageRef.current = pageId ?? null;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const cfgRef = useRef(cfg);
  cfgRef.current = cfg;

  // restore after mount (avoids SSR/client mismatch)
  useEffect(() => {
    const saved = load<ChatMessage[]>(storageKey, "messages", []);
    // a message that was mid-stream when the tab closed can never finish
    setMessages(saved.map((m) => (m.status === "streaming" ? { ...m, status: "stopped" as const } : m)));
    sessionRef.current = load<string | null>(storageKey, "session", null);
  }, [storageKey]);

  useEffect(() => {
    if (!storageKey || typeof window === "undefined") return;
    try {
      window.sessionStorage.setItem(`grovator.chat.${storageKey}.messages`, JSON.stringify(messages.slice(-40)));
      if (sessionRef.current) window.sessionStorage.setItem(`grovator.chat.${storageKey}.session`, JSON.stringify(sessionRef.current));
    } catch {
      /* storage unavailable or full: the chat still works */
    }
  }, [messages, storageKey]);

  const patch = useCallback((id: string, p: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...(typeof p === "function" ? p(m) : p) } : m)));
  }, []);

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || text.length > MAX_CHARS || abortRef.current) return;
      setError(null);
      const botId = uid();
      setMessages((ms) => [
        ...ms,
        { id: uid(), role: "user", text, status: "done" },
        { id: botId, role: "assistant", text: "", status: "streaming" },
      ]);
      const ctl = new AbortController();
      abortRef.current = ctl;
      setBusy(true);
      const startedAt = Date.now();
      let thought = false;
      const think = async () => {   // the first text never appears sooner than MIN_THINK_MS after the question was sent
        if (thought) return;
        thought = true;
        const wait = MIN_THINK_MS - (Date.now() - startedAt);
        if (wait > 0) await sleep(wait, ctl.signal);
      };
      try {
        for await (const ev of streamChat(cfgRef.current, { message: text, sessionId: sessionRef.current, page: pageRef.current, signal: ctl.signal })) {
          if (ev.type === "token" || ev.type === "done") await think();
          if (ev.type === "meta") sessionRef.current = ev.data.session_id;
          else if (ev.type === "token") patch(botId, (m) => ({ text: m.text + ev.data.t }));
          else if (ev.type === "done")
            patch(botId, {
              text: ev.data.answer, // authoritative, already verified server-side
              status: "done",
              requestId: ev.data.request_id,
              sources: ev.data.sources,
              refusedReason: ev.data.refused_reason ?? null,
              grounded: ev.data.grounded,
              tokens: ev.data.tokens_total,
              naiveTokens: ev.data.naive_tokens,
              cacheHit: ev.data.cache_hit ?? null,
              table: ev.data.connection_table ?? null,
              blocks: ev.data.block_stack ?? [],
              code: ev.data.code_blocks ?? [],
              images: ev.data.images ?? [],
              footer: ev.data.footer ?? "",
              warnings: ev.data.warnings ?? [],
            });
          else if (ev.type === "error") patch(botId, { text: ev.data.message, status: "error" });
        }
        patch(botId, (m) => (m.status === "streaming" ? { status: "done" } : {}));
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          patch(botId, (m) => ({ status: "stopped", text: m.text || "(stopped)" }));
        } else {
          const msg = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
          patch(botId, { text: msg, status: "error" });
          setError(msg);
          if (e instanceof ApiError && e.status === 401) onAuthError?.();
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
      }
    },
    [patch, onAuthError],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const newChat = useCallback(async () => {
    abortRef.current?.abort();
    const sid = sessionRef.current;
    sessionRef.current = null;
    setMessages([]);
    setError(null);
    if (storageKey && typeof window !== "undefined") {
      try {
        window.sessionStorage.removeItem(`grovator.chat.${storageKey}.messages`);
        window.sessionStorage.removeItem(`grovator.chat.${storageKey}.session`);
      } catch {
        /* ignore */
      }
    }
    if (sid) await clearSession(cfgRef.current, sid).catch(() => undefined); // best effort: server-side context is wiped too
  }, [storageKey]);

  const rate = useCallback(
    async (id: string, rating: Rating, reason?: FeedbackReason) => {
      const msg = messages.find((m) => m.id === id);
      if (!msg?.requestId) return;
      const before = msg.rating;
      patch(id, { rating });
      try {
        await sendFeedback(cfgRef.current, msg.requestId, rating, reason);
      } catch (e) {
        patch(id, { rating: before });
        setError(e instanceof ApiError ? e.message : "Couldn't save your feedback.");
      }
    },
    [messages, patch],
  );

  // running totals for the token meter (only answers that report both numbers)
  const totals = useMemo(() => {
    let used = 0, naive = 0, answers = 0, cached = 0;
    for (const m of messages) {
      if (m.role === "assistant" && m.status === "done" && m.tokens !== undefined && m.naiveTokens !== undefined) {
        used += m.tokens; naive += m.naiveTokens; answers += 1; if (m.cacheHit) cached += 1;
      }
    }
    return { used, naive, answers, cached, savedPct: naive > 0 ? Math.max(0, Math.round((1 - used / naive) * 100)) : 0 };
  }, [messages]);

  return { messages, busy, error, send, stop, newChat, rate, totals, clearError: () => setError(null) };
}
