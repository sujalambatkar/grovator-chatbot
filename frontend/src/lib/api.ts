import { createSseParser } from "./sse";
import type { ContentResponse, FeedbackReason, Memory, Rating, StreamEvent } from "./types";

export interface ApiConfig {
  baseUrl: string;
  /** Returns the user's Govator JWT. The widget never mints, stores or inspects it. */
  getToken: () => string | null | Promise<string | null>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const FRIENDLY: Record<number, string> = {
  401: "Your session has expired. Please sign in again.",
  403: "You don't have access to this.",
  404: "That item was not found.",
  422: "Please check your message and try again.",
  429: "You're sending messages too fast. Please wait a moment.",
};

async function toError(res: Response): Promise<ApiError> {
  const retry = Number(res.headers.get("Retry-After"));
  let detail = "";
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    /* non-JSON error body */
  }
  const msg =
    res.status === 429 && retry > 0
      ? `You're sending messages too fast. Please wait ${retry} seconds.`
      : FRIENDLY[res.status] ?? (detail || "Something went wrong. Please try again.");
  return new ApiError(msg, res.status, retry > 0 ? retry : undefined);
}

async function authHeaders(cfg: ApiConfig): Promise<Record<string, string>> {
  const token = await cfg.getToken();
  if (!token) throw new ApiError(FRIENDLY[401]!, 401);
  return { Authorization: `Bearer ${token}` };
}

async function call(cfg: ApiConfig, path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${cfg.baseUrl}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(await authHeaders(cfg)), ...(init.headers ?? {}) },
    });
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Can't reach the assistant right now. Check your connection.", 0);
  }
  if (!res.ok) throw await toError(res);
  return res;
}

/** POST /chat/stream and yield typed events as they arrive. Abort with the signal. */
export async function* streamChat(
  cfg: ApiConfig,
  req: { message: string; sessionId?: string | null; page?: string | null; signal?: AbortSignal },
): AsyncGenerator<StreamEvent> {
  const res = await call(cfg, "/chat/stream", {
    method: "POST",
    body: JSON.stringify({
      message: req.message,
      ...(req.sessionId ? { session_id: req.sessionId } : {}),
      ...(req.page ? { page: req.page } : {}),
    }),
    headers: { Accept: "text/event-stream" },
    signal: req.signal,
  });
  if (!res.body) throw new ApiError("The assistant returned an empty response.", 0);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
        let data: unknown;
        try {
          data = JSON.parse(raw.data);
        } catch {
          continue; // ignore a malformed frame rather than crash the chat
        }
        if (raw.event === "meta" || raw.event === "token" || raw.event === "done" || raw.event === "error") {
          yield { type: raw.event, data } as StreamEvent;
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function listMemories(cfg: ApiConfig): Promise<Memory[]> {
  return (await (await call(cfg, "/memories")).json()) as Memory[];
}
export async function deleteMemory(cfg: ApiConfig, id: string): Promise<void> {
  await call(cfg, `/memories/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export async function deleteAllMemories(cfg: ApiConfig): Promise<number> {
  return ((await (await call(cfg, "/memories", { method: "DELETE" })).json()) as { deleted: number }).deleted;
}
export async function clearSession(cfg: ApiConfig, sessionId: string): Promise<void> {
  await call(cfg, `/chat/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}
export async function sendFeedback(
  cfg: ApiConfig,
  requestId: string,
  rating: Rating,
  reason?: FeedbackReason,
): Promise<void> {
  await call(cfg, "/chat/feedback", {
    method: "POST",
    body: JSON.stringify({ request_id: requestId, rating, ...(reason ? { reason } : {}) }),
  });
}

/** The documents this user may read (the same ones the assistant answers from). */
export async function fetchContent(cfg: ApiConfig): Promise<ContentResponse> {
  return (await (await call(cfg, "/content")).json()) as ContentResponse;
}

/** Demo-only auto login (backend must run with DEMO_MODE=true). Not used by a real Govator integration. */
export async function demoSession(baseUrl: string, role: "student" | "teacher"): Promise<{ token: string; role: string; name: string }> {
  let res: Response;
  try {
    res = await fetch(`${baseUrl}/demo/session`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role }) });
  } catch {
    throw new ApiError("Can't reach the assistant API. Is the backend running?", 0);
  }
  if (!res.ok) throw new ApiError("Demo login is not enabled on the backend (set DEMO_MODE=true).", res.status);
  return (await res.json()) as { token: string; role: string; name: string };
}
