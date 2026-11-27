import { Fragment } from "react";
import { parseMarkdown, type Inline } from "@/lib/markdown";
import s from "./widget.module.css";

function renderInline(parts: Inline[]) {
  return parts.map((p, i) =>
    p.t === "bold" ? <strong key={i}>{p.v}</strong> : p.t === "code" ? <code key={i}>{p.v}</code> : <Fragment key={i}>{p.v}</Fragment>,
  );
}

/** Renders model output as React elements only. No dangerouslySetInnerHTML, no links, no images. */
export function SafeText({ text, headingBase = 3 }: { text: string; headingBase?: number }) {
  return (
    <>
      {parseMarkdown(text).map((b, i) =>
        b.type === "p" ? (
          <p key={i}>{renderInline(b.inline)}</p>
        ) : b.type === "h" ? (
          (() => {
            const Tag = `h${Math.min(6, headingBase + b.level - 1)}` as "h3";
            return <Tag key={i}>{renderInline(b.inline)}</Tag>;
          })()
        ) : b.type === "pre" ? (
          <pre key={i} data-testid="code-block">{b.text}</pre>
        ) : b.type === "table" ? (
          <div key={i} className={s.tableWrap}>
            <table className={s.table}>
              <thead><tr>{b.head.map((c, j) => <th key={j} scope="col">{renderInline(c)}</th>)}</tr></thead>
              <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}>{renderInline(c)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : b.type === "ol" ? (
          <ol key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ol>
        ) : (
          <ul key={i}>{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ul>
        ),
      )}
    </>
  );
}
