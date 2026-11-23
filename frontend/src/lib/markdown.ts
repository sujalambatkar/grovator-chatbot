/** Tiny, SAFE markdown subset -> AST. The UI renders it with React elements only (never innerHTML),
 *  so model output can never inject markup or links. Supports paragraphs, ordered/unordered lists, **bold**, `code`. */
export type Inline = { t: "text" | "bold" | "code"; v: string };
export type Block =
  | { type: "p"; inline: Inline[] }
  | { type: "h"; level: 1 | 2 | 3; inline: Inline[] }
  | { type: "pre"; text: string }
  | { type: "ul" | "ol"; items: Inline[][] };

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
  let last = 0;
  for (const m of s.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ t: "text", v: s.slice(last, i) });
    const tok = m[0];
    out.push(tok.startsWith("**") ? { t: "bold", v: tok.slice(2, -2) } : { t: "code", v: tok.slice(1, -1) });
    last = i + tok.length;
  }
  if (last < s.length) out.push({ t: "text", v: s.slice(last) });
  return out;
}

const OL = /^\s*\d{1,2}[.)]\s+(.*)$/;
const HEADING = /^(#{1,3})\s+(.*)$/;
const UL = /^\s*[-*•]\s+(.*)$/;

export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ type: "p", inline: parseInline(para.join(" ")) });
    para = [];
  };
  let fence: string[] | null = null;
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    if (line.trim().startsWith("```")) {
      if (fence) {
        blocks.push({ type: "pre", text: fence.join("\n").replace(/\n+$/, "") });
        fence = null;
      } else {
        flush();
        fence = [];
      }
      continue;
    }
    if (fence) {
      fence.push(line);
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      flush();
      blocks.push({ type: "h", level: Math.min(3, h[1]!.length) as 1 | 2 | 3, inline: parseInline((h[2] ?? "").trim()) });
      continue;
    }
    const ol = OL.exec(line);
    const ul = ol ? null : UL.exec(line);
    const m = ol ?? ul;
    if (m) {
      flush();
      const type = ol ? "ol" : "ul";
      const last = blocks[blocks.length - 1];
      const item = parseInline(m[1] ?? "");
      if (last && last.type === type) last.items.push(item);
      else blocks.push({ type, items: [item] });
    } else if (!line.trim()) {
      flush();
    } else {
      para.push(line.trim());
    }
  }
  if (fence) blocks.push({ type: "pre", text: fence.join("\n").replace(/\n+$/, "") }); // unterminated fence (e.g. a cut-off answer)
  flush();
  return blocks;
}
