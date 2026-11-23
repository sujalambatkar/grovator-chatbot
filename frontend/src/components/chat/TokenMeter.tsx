import s from "./widget.module.css";

export interface Totals {
  used: number;
  naive: number;
  answers: number;
  cached: number;
  savedPct: number;
}

const fmt = (n: number) => n.toLocaleString("en-US");

/** Live proof of the token saving: what this chat has cost vs. a bot that has no retrieval. */
export function TokenMeter({ totals }: { totals: Totals }) {
  if (totals.answers === 0) {
    return (
      <div className={s.meter} data-testid="token-meter">
        <span className={s.meterHint}>Token meter: ask something to see what each answer costs.</span>
      </div>
    );
  }
  const pct = Math.max(2, Math.min(100, Math.round((totals.used / Math.max(1, totals.naive)) * 100)));
  return (
    <div className={s.meter} data-testid="token-meter" aria-live="polite">
      <div className={s.meterRow}>
        <span><b data-testid="meter-used">{fmt(totals.used)}</b> tokens used</span>
        <span className={s.meterSave} data-testid="meter-saved">{totals.savedPct}% saved</span>
        <span>naive bot ≈ <b data-testid="meter-naive">{fmt(totals.naive)}</b></span>
      </div>
      <div className={s.bar} role="img" aria-label={`Used ${fmt(totals.used)} of about ${fmt(totals.naive)} tokens`}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <details className={s.meterWhy}>
        <summary>How is this measured?</summary>
        <p>
          <b>Used</b> is what this chat really sent to the model (router + prompt + answer), as reported by the API
          {totals.cached > 0 ? `, with ${totals.cached} answer${totals.cached > 1 ? "s" : ""} served from cache at 0 tokens` : ""}.
          <b> Naive bot</b> is an estimate of a chatbot with no retrieval: to answer anything about the platform it would
          send every course page, block and tool description <em>plus the whole chat so far</em> with each message.
        </p>
      </details>
    </div>
  );
}
