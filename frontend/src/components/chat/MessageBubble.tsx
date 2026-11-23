"use client";
import { useState } from "react";
import type { ChatMessage, FeedbackReason, Rating } from "@/lib/types";
import { SafeText } from "./SafeText";
import s from "./widget.module.css";

const REASONS: { id: FeedbackReason; label: string }[] = [
  { id: "wrong", label: "Wrong" },
  { id: "unclear", label: "Unclear" },
  { id: "unhelpful", label: "Not helpful" },
  { id: "unsafe", label: "Unsafe" },
];

interface Props {
  showTokens?: boolean;
  msg: ChatMessage;
  onRate: (id: string, rating: Rating, reason?: FeedbackReason) => void;
}

export function MessageBubble({ msg, onRate, showTokens = false }: Props) {
  const [reason, setReason] = useState<FeedbackReason | null>(null);

  if (msg.role === "user") {
    return (
      <div className={`${s.row} ${s.rowUser}`}>
        <div className={`${s.bubble} ${s.user}`} data-testid="msg-user">{msg.text}</div>
      </div>
    );
  }

  const waiting = msg.status === "streaming" && !msg.text;
  const canRate = msg.status === "done" && !!msg.requestId;
  return (
    <div className={`${s.row} ${s.rowBot}`}>
      <div className={`${s.bubble} ${s.bot} ${msg.status === "error" ? s.err : ""}`} data-testid="msg-bot" aria-busy={msg.status === "streaming"}>
        {waiting ? (
          <span className={s.dots} role="status" aria-label="The assistant is typing"><i /><i /><i /></span>
        ) : (
          <SafeText text={msg.text} />
        )}
      </div>
      {msg.status === "done" && (
        <div className={s.meta}>
          {msg.sources && msg.sources.length > 0 && <span data-testid="sources">Source: {msg.sources.join(", ")}</span>}
          {showTokens && msg.tokens !== undefined && (
            <span data-testid="msg-tokens" className={s.tokenChip}>
              {msg.tokens === 0
                ? msg.cacheHit ? "0 tokens (from cache)" : "0 tokens (no model call)"
                : `${msg.tokens.toLocaleString("en-US")} tokens`}
              {msg.naiveTokens ? ` · naive ≈ ${msg.naiveTokens.toLocaleString("en-US")}` : ""}
            </span>
          )}
          {canRate && (
            <span className={s.fbRow} role="group" aria-label="Was this answer helpful?">
              <button type="button" className={s.fb} aria-pressed={msg.rating === "up"} aria-label="Helpful" data-testid="fb-up"
                onClick={() => onRate(msg.id, "up")}>Helpful</button>
              <button type="button" className={s.fb} aria-pressed={msg.rating === "down"} aria-label="Not helpful" data-testid="fb-down"
                onClick={() => onRate(msg.id, "down", reason ?? undefined)}>Not helpful</button>
            </span>
          )}
        </div>
      )}
      {canRate && msg.rating === "down" && (
        <div className={s.reasons} role="group" aria-label="What was wrong?">
          {REASONS.map((r) => (
            <button key={r.id} type="button" className={s.chip} aria-pressed={reason === r.id} data-testid={`reason-${r.id}`}
              onClick={() => { setReason(r.id); onRate(msg.id, "down", r.id); }}>{r.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}
