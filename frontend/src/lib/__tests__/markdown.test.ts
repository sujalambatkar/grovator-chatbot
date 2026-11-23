import { describe, expect, it } from "vitest";
import { parseMarkdown } from "../markdown";

describe("safe markdown", () => {
  it("parses numbered steps, bold and code", () => {
    const b = parseMarkdown("Here is how:\n\n1. Press **Run**\n2. Open `My Projects`\n3. Done");
    expect(b[0]).toEqual({ type: "p", inline: [{ t: "text", v: "Here is how:" }] });
    expect(b[1]).toMatchObject({ type: "ol" });
    const ol = b[1] as { items: { t: string; v: string }[][] };
    expect(ol.items).toHaveLength(3);
    expect(ol.items[0]).toEqual([{ t: "text", v: "Press " }, { t: "bold", v: "Run" }]);
    expect(ol.items[1]![1]).toEqual({ t: "code", v: "My Projects" });
  });

  it("parses bullet lists with -, * and •", () => {
    const b = parseMarkdown("- a\n* b\n• c");
    expect(b).toEqual([{ type: "ul", items: [[{ t: "text", v: "a" }], [{ t: "text", v: "b" }], [{ t: "text", v: "c" }]] }]);
  });

  it("never produces HTML or links: markup stays inert text", () => {
    const evil = '<img src=x onerror=alert(1)> [click](javascript:alert(1)) <script>alert(1)</script>';
    const b = parseMarkdown(evil);
    expect(b).toHaveLength(1);
    const inline = (b[0] as { inline: { t: string; v: string }[] }).inline;
    expect(inline.every((i) => i.t === "text")).toBe(true);
    expect(inline.map((i) => i.v).join("")).toBe(evil); // preserved verbatim as text; React escapes it
  });

  it("leaves unmatched markers alone and handles empty input", () => {
    expect(parseMarkdown("2 ** 3 and `unclosed")).toEqual([{ type: "p", inline: [{ t: "text", v: "2 ** 3 and `unclosed" }] }]);
    expect(parseMarkdown("")).toEqual([]);
  });

  it("separates paragraphs on blank lines and joins soft-wrapped lines", () => {
    expect(parseMarkdown("one\ntwo\n\nthree")).toEqual([
      { type: "p", inline: [{ t: "text", v: "one two" }] },
      { type: "p", inline: [{ t: "text", v: "three" }] },
    ]);
  });
});

describe("headings and code blocks", () => {
  it("parses headings and fenced block programs", () => {
    const b = parseMarkdown("# Goal\nRead the sensor.\n\n# Example program\n```\nwhen green flag clicked\nforever\n  display text (temperature value)\n```\n\n## Tip\nWait 1 second.");
    expect(b.map((x) => x.type)).toEqual(["h", "p", "h", "pre", "h", "p"]);
    expect(b[0]).toMatchObject({ type: "h", level: 1 });
    expect(b[3]).toEqual({ type: "pre", text: "when green flag clicked\nforever\n  display text (temperature value)" });
    expect(b[4]).toMatchObject({ level: 2 });
  });

  it("keeps markup inside code fences inert and survives an unterminated fence", () => {
    const b = parseMarkdown("```\n<script>alert(1)</script> **not bold**\n");
    expect(b).toEqual([{ type: "pre", text: "<script>alert(1)</script> **not bold**" }]);
  });

  it("does not treat # inside text as a heading", () => {
    expect(parseMarkdown("use C# or the # key")[0]).toMatchObject({ type: "p" });
  });
});
