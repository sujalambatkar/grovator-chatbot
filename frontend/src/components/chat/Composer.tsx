"use client";
import { useEffect, useRef, useState } from "react";
import { MAX_CHARS } from "@/hooks/useChat";
import s from "./widget.module.css";

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  focusSignal: number;
}

export function Composer({ busy, onSend, onStop, focusSignal }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { ref.current?.focus(); }, [focusSignal]);

  const over = text.length > MAX_CHARS;
  const submit = () => {
    if (!text.trim() || over || busy) return;
    onSend(text);
    setText("");
  };

  return (
    <>
      {text.length > MAX_CHARS * 0.8 && (
        <div className={`${s.counter} ${over ? s.counterWarn : ""}`} aria-live="polite">{text.length}/{MAX_CHARS}</div>
      )}
      <form className={s.composer} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className={s.srOnly} htmlFor="grovator-chat-input">Message</label>
        <textarea
          id="grovator-chat-input" ref={ref} className={s.textarea} rows={1} value={text} placeholder="Ask about kits, block coding…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
          data-testid="composer-input"
        />
        {busy ? (
          <button type="button" className={s.send} onClick={onStop} data-testid="stop">Stop</button>
        ) : (
          <button type="submit" className={s.send} disabled={!text.trim() || over} data-testid="send">Send</button>
        )}
      </form>
    </>
  );
}
