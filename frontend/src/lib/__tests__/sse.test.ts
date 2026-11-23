import { describe, expect, it } from "vitest";
import { createSseParser } from "../sse";

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe("SSE parser", () => {
  it("parses complete frames", () => {
    const p = createSseParser();
    const out = p.push(frame("meta", { a: 1 }) + frame("token", { t: "hi " }));
    expect(out.map((e) => e.event)).toEqual(["meta", "token"]);
    expect(JSON.parse(out[1]!.data)).toEqual({ t: "hi " });
  });

  it("reassembles frames split at arbitrary byte boundaries", () => {
    const whole = frame("token", { t: "héllo wörld" }) + frame("done", { answer: "x" });
    for (let cut = 1; cut < whole.length; cut += 3) {
      const p = createSseParser();
      const out = [...p.push(whole.slice(0, cut)), ...p.push(whole.slice(cut))];
      expect(out.map((e) => e.event)).toEqual(["token", "done"]);
      expect(JSON.parse(out[0]!.data).t).toBe("héllo wörld");
    }
  });

  it("handles CRLF line endings, comments and multi-line data", () => {
    const p = createSseParser();
    const out = p.push(": keep-alive\r\n\r\nevent: token\r\ndata: a\r\ndata: b\r\n\r\n");
    expect(out).toEqual([{ event: "token", data: "a\nb" }]);
  });

  it("holds back an incomplete trailing frame", () => {
    const p = createSseParser();
    expect(p.push("event: token\ndata: {\"t\":\"x\"}")).toEqual([]);
    expect(p.push("\n\n")).toHaveLength(1);
  });

  it("drops frames without data", () => {
    expect(createSseParser().push("event: ping\n\n")).toEqual([]);
  });
});
