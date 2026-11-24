"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiConfig } from "@/lib/api";
import { useChat } from "@/hooks/useChat";
import { Composer } from "./Composer";
import { ChatIcon, CloseIcon } from "./Icons";
import { MemoryPanel } from "./MemoryPanel";
import { MessageBubble } from "./MessageBubble";
import { TokenMeter } from "./TokenMeter";
import s from "./widget.module.css";

export interface ChatWidgetProps {
  /** Base URL of the Grovator Assistant API (FastAPI). */
  apiUrl: string;
  /** Returns the signed-in user's JWT. */
  getToken: () => string | null | Promise<string | null>;
  title?: string;
  /** Persist the conversation per tab under this key (use the user id). Omit to keep it in memory only. */
  storageKey?: string;
  defaultOpen?: boolean;
  onAuthError?: () => void;
  /** `source` of the content page the user is viewing, so "explain this lesson" works */
  pageId?: string | null;
  /** starter questions shown in an empty chat (make them page-specific) */
  suggestions?: string[];
  showMemory?: boolean;
  /** live tokens-used vs naive-bot meter */
  showTokenMeter?: boolean;
}

const SUGGESTIONS = ["Which sensors can I connect?", "How do I connect the ultrasonic sensor?", "Which pins support analog input?", "What are the wiring rules?"];

export function ChatWidget({
  apiUrl, getToken, title = "Grovator Assistant", storageKey, defaultOpen = false, onAuthError, pageId,
  suggestions = SUGGESTIONS, showMemory = true, showTokenMeter = false,
}: ChatWidgetProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [view, setView] = useState<"chat" | "memory">("chat");
  const [focusSignal, setFocusSignal] = useState(0);
  const cfg: ApiConfig = useMemo(() => ({ baseUrl: apiUrl.replace(/\/$/, ""), getToken }), [apiUrl, getToken]);
  const { messages, busy, error, send, stop, newChat, rate, totals, clearError } = useChat(cfg, { storageKey, onAuthError, pageId });
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [messages, view]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const toggle = () => { setOpen((o) => !o); setFocusSignal((n) => n + 1); };

  return (
    <div className={s.root}>
      {open && (
        <section className={s.panel} role="dialog" aria-label={title} data-testid="chat-panel">
          <header className={s.header}>
            <span className={s.title}>{title}</span>
            {showMemory && (
              <button type="button" className={s.iconBtn} aria-pressed={view === "memory"} data-testid="memory-toggle"
                onClick={() => setView((v) => (v === "chat" ? "memory" : "chat"))} title="What I remember">Memory</button>
            )}
            <button type="button" className={s.iconBtn} onClick={() => { void newChat(); setView("chat"); }} data-testid="new-chat" title="Start a new chat">New</button>
            <button type="button" className={s.iconBtn} onClick={toggle} aria-label="Close chat"><CloseIcon size={16} /></button>
          </header>

          {showTokenMeter && view === "chat" && <TokenMeter totals={totals} />}
          <div className={s.body} role={view === "chat" ? "log" : undefined} aria-live="polite" aria-relevant="additions text">
            {view === "memory" ? (
              <MemoryPanel cfg={cfg} />
            ) : messages.length === 0 ? (
              <div className={s.welcome} data-testid="welcome">
                Hi! I can help you connect sensors and modules to the Grovator ESP32 IoT Gateway.
                <div className={s.suggestions}>
                  {suggestions.map((q) => (
                    <button key={q} type="button" className={s.chip} onClick={() => void send(q)}>{q}</button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => <MessageBubble key={m.id} msg={m} onRate={rate} showTokens={showTokenMeter} />)
            )}
            <div ref={endRef} />
          </div>

          {error && (
            <div className={s.notice} role="alert" data-testid="chat-error">
              <span>{error}</span>
              <button type="button" className={s.link} onClick={clearError}>Dismiss</button>
            </div>
          )}
          {view === "chat" && <Composer busy={busy} onSend={(t) => void send(t)} onStop={stop} focusSignal={focusSignal} />}
        </section>
      )}
      <button type="button" className={s.launcher} onClick={toggle} aria-label={open ? "Close Grovator Assistant" : "Open Grovator Assistant"}
        aria-expanded={open} data-testid="launcher">{open ? <CloseIcon /> : <ChatIcon />}</button>
    </div>
  );
}
