"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatWidget } from "@/components/chat";
import { SafeText } from "@/components/chat/SafeText";
import { ApiError, demoSession, fetchContent } from "@/lib/api";
import type { ContentDoc, ContentResponse } from "@/lib/types";

const API_URL = (process.env.NEXT_PUBLIC_CHATBOT_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const ROLE = "student" as const; // the demo always runs as a student; access control is enforced by the server
const DEFAULT_PAGE = "devices/ultrasonic_sensor";

// Suggestions come from the server per page (doc.suggestions): only questions answered by pre-written answers, so clicking one costs 0 tokens.
const GENERIC = ["Which sensors can I connect?", "What are the wiring rules?", "Which pins support analog input?", "What is the gateway?"];

export function DemoHost() {
  const [content, setContent] = useState<ContentResponse | null>(null);
  const [selected, setSelected] = useState<string>(DEFAULT_PAGE);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  const getToken = useCallback(() => tokenRef.current, []);

  useEffect(() => {
    let cancelled = false;
    setContent(null); setError(null);
    (async () => {
      try {
        const s = await demoSession(API_URL, ROLE);
        if (cancelled) return;
        tokenRef.current = s.token;
        const c = await fetchContent({ baseUrl: API_URL, getToken });
        if (cancelled) return;
        setContent(c);
        setSelected((prev) => (c.docs.some((d) => d.source === prev) ? prev : c.docs.find((d) => d.category === "devices")?.source ?? c.docs[0]?.source ?? ""));
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Something went wrong loading the page.");
      }
    })();
    return () => { cancelled = true; };
  }, [getToken]);

  const groups = useMemo(() => {
    const out: { key: string; label: string; docs: ContentDoc[] }[] = [];
    for (const d of content?.docs ?? []) {
      let g = out.find((x) => x.key === d.category);
      if (!g) { g = { key: d.category, label: content?.categories[d.category] ?? d.category, docs: [] }; out.push(g); }
      g.docs.push(d);
    }
    return out;
  }, [content]);
  const doc = content?.docs.find((d) => d.source === selected);

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Grovator <b>Learn</b></span>
      </header>

      {error && (
        <div className="notice" role="alert" data-testid="load-error">
          <b>{error}</b>
          <p>Start the backend with <code>DEMO_MODE=true</code> and check <code>NEXT_PUBLIC_CHATBOT_API_URL</code> ({API_URL}).</p>
        </div>
      )}

      {content && (
        <div className="layout">
          <nav className="nav" aria-label="Course content" data-testid="nav">
            {groups.map((g) => (
              <div key={g.key}>
                <h2>{g.label}</h2>
                <ul>
                  {g.docs.map((d) => (
                    <li key={d.source}>
                      <button type="button" className={d.source === selected ? "navbtn on" : "navbtn"} aria-current={d.source === selected ? "page" : undefined}
                        onClick={() => setSelected(d.source)} data-testid="nav-item">{d.title}</button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
          <select className="navselect" aria-label="Choose a page" value={selected} onChange={(e) => setSelected(e.target.value)}>
            {groups.map((g) => (
              <optgroup key={g.key} label={g.label}>{g.docs.map((d) => <option key={d.source} value={d.source}>{d.title}</option>)}</optgroup>
            ))}
          </select>
          <article className="article" data-testid="article">
            {doc && (
              <>
                <p className="crumb">{content.categories[doc.category] ?? doc.category}</p>
                <h1 data-testid="article-title">{doc.title}</h1>
                <SafeText text={doc.body} headingBase={2} />
              </>
            )}
          </article>
        </div>
      )}

      {content && doc && (
        <ChatWidget apiUrl={API_URL} getToken={getToken} storageKey="demo-student" pageId={doc.source}
          suggestions={doc.suggestions?.length ? doc.suggestions : GENERIC} showMemory={false} showTokenMeter defaultOpen
          title="Grovator Assistant" />
      )}
    </div>
  );
}
