"use client";
import { useEffect, useState } from "react";
import type { BlockStackItem, CodeBlock, ConnectionTable as Table, ImageRef, SafetyWarning } from "@/lib/types";
import s from "./widget.module.css";

/** Wiring table built from the knowledge files. Unconfirmed rows are marked. */
export function ConnectionTable({ table }: { table: Table }) {
  return (
    <figure className={s.richCard} data-testid="connection-table">
      <figcaption className={s.richTitle}>{table.title}</figcaption>
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead><tr>{table.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i}>
                <td>{r.sensor_pin}</td>
                <td>{r.kit_pin}{r.verified ? "" : " (?)"}</td>
                <td>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.note && <div className={s.richNote}>{table.note}</div>}
    </figure>
  );
}

/** The blocks to stack, top to bottom. */
export function BlockStack({ blocks }: { blocks: BlockStackItem[] }) {
  return (
    <ol className={s.stack} data-testid="block-stack" aria-label="Blocks, top to bottom">
      {blocks.map((b) => (
        <li key={b.order} className={s.stackBlock} title={b.description}>
          <span className={s.stackNo}>{b.order}</span> {b.title}
          {b.description && <span className={s.stackDesc}>{b.description}</span>}
        </li>
      ))}
    </ol>
  );
}

export function CodeCard({ block }: { block: CodeBlock }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(block.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the text is still selectable */
    }
  };
  return (
    <figure className={s.richCard} data-testid="code-card">
      <figcaption className={s.codeHead}>
        <span className={s.richTitle}>{block.title}</span>
        <button type="button" className={s.fb} onClick={() => void copy()} data-testid="copy-code" aria-live="polite">{copied ? "Copied" : "Copy"}</button>
      </figcaption>
      <pre className={s.codePre}><code>{block.code}</code></pre>
      {block.note && <div className={s.richNote}>{block.note}</div>}
    </figure>
  );
}

/** Thumbnails that open full size (click, Escape or the Close button to leave). */
export function Images({ images, baseUrl }: { images: ImageRef[]; baseUrl: string }) {
  const [open, setOpen] = useState<ImageRef | null>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(null); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open]);
  const src = (i: ImageRef) => (i.url.startsWith("http") ? i.url : `${baseUrl}${i.url}`);
  return (
    <>
      <div className={s.images}>
        {images.map((im) => (
          <button key={im.url} type="button" className={s.thumb} onClick={() => setOpen(im)} data-testid="image-thumb" aria-label={`Enlarge: ${im.caption}`}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src(im)} alt={im.caption} loading="lazy" />
            <span>{im.caption}{im.verified ? "" : " (not confirmed)"}</span>
          </button>
        ))}
      </div>
      {open && (
        <div className={s.lightbox} role="dialog" aria-modal="true" aria-label={open.caption} data-testid="lightbox" onClick={() => setOpen(null)}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src(open)} alt={open.caption} />
          <div className={s.lightboxCap}>{open.caption}</div>
          <button type="button" className={s.send} onClick={() => setOpen(null)}>Close</button>
        </div>
      )}
    </>
  );
}

/** Collapsed by default: the answer first, the evidence on request. */
export function Sources({ sources }: { sources: string[] }) {
  return (
    <details className={s.sources} data-testid="sources">
      <summary>Sources ({sources.length})</summary>
      <ul>{sources.map((x) => <li key={x}>{x}</li>)}</ul>
    </details>
  );
}

/** Safety reminders for students (decided by the backend from config/safety.yaml): one quiet grey line each, danger first. */
export function Warnings({ warnings }: { warnings: SafetyWarning[] }) {
  return (
    <div className={s.warnList} data-testid="safety-warnings" role="note" aria-label="Safety">
      {warnings.map((w) => (
        <div key={w.id} className={s.warn} data-testid={`warning-${w.id}`}>
          <span aria-hidden="true">{w.level === "danger" ? "\u26D4" : "\u26A0"}</span> {w.text}
        </div>
      ))}
    </div>
  );
}
