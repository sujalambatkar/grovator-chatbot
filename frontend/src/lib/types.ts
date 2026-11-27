export interface MetaEvent {
  session_id: string;
  intent: string;
  shortcut?: boolean;
}

/** Structured parts of an application answer, built by the server from the knowledge files (never by the model). */
export interface ConnectionTable { title: string; columns: string[]; rows: { sensor_pin: string; kit_pin: string; note: string; verified: boolean }[]; note?: string }
export interface BlockStackItem { order: number; title: string; description: string }
export interface CodeBlock { title: string; language: string; code: string; note?: string }
export interface ImageRef { url: string; caption: string; verified: boolean }

export interface DoneEvent {
  answer: string;
  session_id: string;
  intent: string;
  grounded: boolean;
  sources: string[];
  refused_reason?: string | null;
  request_id?: string;
  cache_hit?: string | null;
  truncated?: boolean;
  tokens_total?: number;
  naive_tokens?: number;
  connection_table?: ConnectionTable | null;
  block_stack?: BlockStackItem[];
  code_blocks?: CodeBlock[];
  images?: ImageRef[];
  footer?: string;
  warnings?: SafetyWarning[];
}

export type StreamEvent =
  | { type: "meta"; data: MetaEvent }
  | { type: "token"; data: { t: string } }
  | { type: "done"; data: DoneEvent }
  | { type: "error"; data: { message: string } };

export interface Memory {
  id: string;
  text: string;
  type: string;
  created_at: string;
  last_used_at: string;
}

export type Rating = "up" | "down";
export type FeedbackReason = "wrong" | "unclear" | "unhelpful" | "unsafe" | "other";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: "streaming" | "done" | "error" | "stopped";
  requestId?: string;
  sources?: string[];
  refusedReason?: string | null;
  grounded?: boolean;
  rating?: Rating;
  /** tokens this answer cost (router + answer in/out) and the estimate for a bot without retrieval */
  tokens?: number;
  naiveTokens?: number;
  cacheHit?: string | null;
  table?: ConnectionTable | null;
  blocks?: BlockStackItem[];
  code?: CodeBlock[];
  images?: ImageRef[];
  footer?: string;
  warnings?: SafetyWarning[];
}

export interface SafetyWarning {
  id: string;
  level: "danger" | "caution";
  title: string;
  text: string;
}

export interface ContentDoc {
  source: string;
  title: string;
  category: string;
  order: number;
  body: string;
  suggestions?: string[];
}

export interface ContentResponse {
  categories: Record<string, string>;
  docs: ContentDoc[];
}
