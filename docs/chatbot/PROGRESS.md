# Build progress

| Phase | Status | Headline result |
|---|---|---|
| 0 Discovery | done (standalone: no Grovator repo available) | assumptions recorded in DISCOVERY.md |
| 1 Ingestion + RAG | done | 15/15 retrieval Q&A, hybrid BM25+vector, RBAC-filtered |
| 2 Conversation context | done | Redis sessions, router + rewrite, SSE; 12/12 live follow-ups |
| 3 Long-term memory | done | 0 cross-user leakage (mutation-verified), PII/health filters |
| 4 Guardrails + RBAC + tools | done | 80-attack red team all blocked; 34 live attacks, 0 leaks |
| 5 Token optimisation + observability | done | 1,147 -> 516 tokens/turn (-55%), usage endpoint, JSONL logs |
| 6 Widget + eval + README | done | 18 browser E2E, 193-item eval, Mermaid architecture |
