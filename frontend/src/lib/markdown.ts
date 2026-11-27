/** Tiny, SAFE markdown subset -> AST. The UI renders it with React elements only (never innerHTML),
 *  so model output can never inject markup or links. Supports paragraphs, ordered/unordered lists, **bold**, `code`. */
export type Inline = { t: "text" | "bold" | "code"; v: string };
export type Block =
  | { type: "p"; inline: Inline[] }
  | { type: "h"; level: 1 | 2 | 3; inline: Inline[] }
  | { type: "pre"; text: string }
  | { type: "ul" | "ol"; items: Inline[][] }
  | { type: "table"; head: Inline[][]; rows: Inline[][][] };

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

const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const cells = (line: string) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => parseInline(c.replace(/<\s*br\s*\/?\s*>/gi, " ").trim()));

export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ type: "p", inline: parseInline(para.join(" ")) });
    para = [];
  };
  let fence: string[] | null = null;
  // <br> is a line break everywhere except inside a table row (its cells are split later)
  const lines = text.replace(/\r\n/g, "\n").split("\n").flatMap((l) => (TABLE_ROW.test(l) ? [l] : l.replace(/<\s*br\s*\/?\s*>/gi, "\n").split("\n")));
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li]!;
    if (!fence && TABLE_ROW.test(line) && TABLE_SEP.test(lines[li + 1] ?? "")) {   // a markdown table: render it as a table, never as raw pipes
      flush();
      const head = cells(line);
      const rows: Inline[][][] = [];
      li += 2;
      while (li < lines.length && TABLE_ROW.test(lines[li]!)) rows.push(cells(lines[li++]!));
      li--;
      blocks.push({ type: "table", head, rows });
      continue;
    }
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
