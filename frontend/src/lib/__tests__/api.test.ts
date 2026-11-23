import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteMemory, listMemories, sendFeedback, streamChat, type ApiConfig } from "../api";

const cfg: ApiConfig = { baseUrl: "http://api.test", getToken: () => "TOKEN123" };
const enc = new TextEncoder();
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function sse(chunks: string[], init: ResponseInit = { status: 200 }): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      chunks.forEach((x) => c.enqueue(enc.encode(x)));
      c.close();
    },
  });
  return new Response(stream, { ...init, headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function collect(gen: AsyncGenerator<unknown>) {
  const out: unknown[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe("streamChat", () => {
  it("sends the bearer token and JSON body, and yields typed events across split chunks", async () => {
    const whole = frame("meta", { session_id: "s", intent: "platform_faq" }) + frame("token", { t: "Press " }) +
      frame("token", { t: "Run." }) + frame("done", { answer: "Press Run.", session_id: "s", intent: "platform_faq", grounded: true, sources: [] });
    const spy = vi.fn().mockResolvedValue(sse([whole.slice(0, 40), whole.slice(40, 120), whole.slice(120)]));
    vi.stubGlobal("fetch", spy);
    const events = (await collect(streamChat(cfg, { message: "hi there", sessionId: null }))) as { type: string }[];
    expect(events.map((e) => e.type)).toEqual(["meta", "token", "token", "done"]);
    const [url, init] = spy.mock.calls[0]!;
    expect(url).toBe("http://api.test/chat/stream");
    expect(init.headers.Authorization).toBe("Bearer TOKEN123");
    expect(JSON.parse(init.body)).toEqual({ message: "hi there" }); // no session_id key when none yet
  });

  it("includes session_id once known", async () => {
    const spy = vi.fn().mockResolvedValue(sse([]));
    vi.stubGlobal("fetch", spy);
    await collect(streamChat(cfg, { message: "x", sessionId: "abc12345" }));
    expect(JSON.parse(spy.mock.calls[0]![1].body)).toEqual({ message: "x", session_id: "abc12345" });
  });

  it("ignores malformed frames and unknown events instead of crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sse(["event: token\ndata: {oops\n\n", "event: weird\ndata: {}\n\n", frame("token", { t: "ok" })])));
    const events = (await collect(streamChat(cfg, { message: "x" }))) as { type: string; data: { t: string } }[];
    expect(events).toHaveLength(1);
    expect(events[0]!.data.t).toBe("ok");
  });

  it("maps HTTP errors to friendly ApiErrors", async () => {
    const cases: [number, Record<string, string>, string, RegExp][] = [
      [401, {}, "Invalid or missing credentials", /sign in again/i],           // server wording replaced by a friendly one
      [429, { "Retry-After": "12" }, "Too many requests.", /12 seconds/],
      [422, {}, "[{...pydantic internals...}]", /check your message/i],
      [503, {}, "The assistant is busy right now. Please try again.", /busy/i], // our own friendly detail is passed through
      [500, {}, "", /Something went wrong/i],
    ];
    for (const [status, headers, detail, re] of cases) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status, headers })));
      const err = await collect(streamChat(cfg, { message: "x" })).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(status);
      expect(err.message).toMatch(re);
      if (status === 429) expect(err.retryAfter).toBe(12);
    }
  });

  it("does not call the network without a token", async () => {
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    const err = await collect(streamChat({ ...cfg, getToken: () => null }, { message: "x" })).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(spy).not.toHaveBeenCalled();
  });

  it("turns a network failure into a friendly error but lets aborts through", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await collect(streamChat(cfg, { message: "x" })).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    expect(await collect(streamChat(cfg, { message: "x" })).catch((e) => e)).toBe(abort);
  });
});

describe("memory + feedback calls", () => {
  it("uses the right verbs/paths and URL-encodes ids", async () => {
    const spy = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]", { status: 200 })));
    vi.stubGlobal("fetch", spy);
    await listMemories(cfg);
    await deleteMemory(cfg, "a/b c");
    await sendFeedback(cfg, "f".repeat(32), "down", "unclear");
    const calls = spy.mock.calls.map(([u, i]) => [u, i?.method ?? "GET", i?.body]);
    expect(calls[0]).toEqual(["http://api.test/memories", "GET", undefined]);
    expect(calls[1]).toEqual(["http://api.test/memories/a%2Fb%20c", "DELETE", undefined]);
    expect(calls[2]![0]).toBe("http://api.test/chat/feedback");
    expect(JSON.parse(calls[2]![2] as string)).toEqual({ request_id: "f".repeat(32), rating: "down", reason: "unclear" });
  });
});
