"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, deleteAllMemories, deleteMemory, listMemories, type ApiConfig } from "@/lib/api";
import type { Memory } from "@/lib/types";
import s from "./widget.module.css";

export function MemoryPanel({ cfg }: { cfg: ApiConfig }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Something went wrong.");
  const load = useCallback(() => listMemories(cfg).then((m) => { setItems(m); setError(null); }).catch(fail), [cfg]);
  useEffect(() => { void load(); }, [load]);

  const remove = async (id: string) => {
    const prev = items;
    setItems((it) => it?.filter((m) => m.id !== id) ?? null);
    try { await deleteMemory(cfg, id); } catch (e) { setItems(prev); fail(e); }
  };
  const removeAll = async () => {
    try { await deleteAllMemories(cfg); setItems([]); setConfirmAll(false); } catch (e) { fail(e); }
  };

  return (
    <section aria-label="What the assistant remembers" data-testid="memory-panel">
      <p className={s.welcome}>
        These are short notes the assistant keeps to help you better next time, like your preferred answer style or the kit you are working on.
        Only you can see them, and you can delete any of them.
      </p>
      {error && <div className={s.notice} role="alert">{error}</div>}
      {items === null && !error && <p className={s.welcome}>Loading…</p>}
      {items?.length === 0 && <p className={s.welcome} data-testid="memory-empty">Nothing is remembered yet.</p>}
      <ul className={s.mem}>
        {items?.map((m) => (
          <li key={m.id} className={s.memItem} data-testid="memory-item">
            <span><span className={s.memType}>{m.type}</span><br />{m.text}</span>
            <button type="button" className={s.link} onClick={() => remove(m.id)} aria-label={`Forget: ${m.text}`}>Forget</button>
          </li>
        ))}
      </ul>
      {items && items.length > 1 && (
        <p>
          {confirmAll ? (
            <>
              <span className={s.danger}>Delete all {items.length} notes? </span>
              <button type="button" className={s.link} onClick={removeAll} data-testid="memory-confirm-all">Yes, delete all</button>{" "}
              <button type="button" className={s.link} onClick={() => setConfirmAll(false)}>Cancel</button>
            </>
          ) : (
            <button type="button" className={`${s.link} ${s.danger}`} onClick={() => setConfirmAll(true)} data-testid="memory-delete-all">Delete all</button>
          )}
        </p>
      )}
    </section>
  );
}
