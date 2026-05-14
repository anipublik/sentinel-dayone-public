import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { DecisionTrail } from "./components/DecisionTrail";
import type { Citation, FeedItem, Persona, TraversalGraph, WorkflowMode } from "./types";

const FALLBACK_PERSONAS: Persona[] = [
  {
    employee_id: "sre@example.com",
    full_name: "Alex Chen",
    team: "Platform Engineering",
    role: "Site Reliability Engineer",
    sample_query: "why does payments-svc have a 5% error budget?",
  },
  {
    employee_id: "backend@example.com",
    full_name: "Jordan Rivera",
    team: "Payments",
    role: "Software Engineer",
    sample_query: "which PR added the circuit breaker after the outage?",
  },
  {
    employee_id: "qa@example.com",
    full_name: "Sam Patel",
    team: "Quality Engineering",
    role: "QA Engineer",
    sample_query: "what ticket tracks the post-outage reliability work?",
  },
];

const MODE_COPY: Record<WorkflowMode, { title: string; sub: string; placeholder: string }> = {
  ask: {
    title: "Context Q&A",
    sub: "Answers grounded in your engineering knowledge graph, scoped to this hire's access.",
    placeholder: "Ask about architecture, incidents, ownership, access…",
  },
  provision: {
    title: "Day-one provisioning",
    sub: "Grant access, surface backlog, and narrate the result for the selected hire.",
    placeholder: "Press send to run provisioning (optional notes)…",
  },
  ingest: {
    title: "Ingestion",
    sub: "Queue a connector sync into Neo4j. Use github, slack, linear, confluence, or all.",
    placeholder: "Source: github | slack | linear | confluence | all",
  },
};

function uid() {
  return crypto.randomUUID();
}

function initials(name: string) {
  return name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export default function App() {
  const [personas, setPersonas] = useState<Persona[]>(FALLBACK_PERSONAS);
  const [employeeId, setEmployeeId] = useState("sre@example.com");
  const [mode, setMode] = useState<WorkflowMode>("ask");
  const [drill, setDrill] = useState<Array<{ id: string; query: string; hint: string }>>([]);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [online, setOnline] = useState(false);
  const [catalog, setCatalog] = useState("—");
  const [trail, setTrail] = useState<TraversalGraph | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const feedEnd = useRef<HTMLDivElement>(null);

  const persona = personas.find((p) => p.employee_id === employeeId) ?? personas[0];
  const copy = MODE_COPY[mode];
  const hasFeed = feed.length > 0;

  const push = useCallback((item: FeedItem) => setFeed((f) => [...f, item]), []);
  const notify = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  }, []);

  useEffect(() => {
    api.personas().then(setPersonas).catch(() => setPersonas(FALLBACK_PERSONAS));
    api.drillQuestions().then(setDrill).catch(() => {});
  }, []);

  useEffect(() => {
    const tick = () =>
      api
        .health()
        .then((h) => {
          setOnline(h.status === "ok");
          setCatalog(h.catalog ?? "—");
        })
        .catch(() => setOnline(false));
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [feed, busy]);

  async function handleAsk(query: string) {
    push({ id: uid(), type: "user", text: query, employeeId });
    setBusy(true);
    try {
      const data = await api.ask(employeeId, query);
      push({
        id: uid(),
        type: "assistant",
        text: data.answer,
        meta: `Intent: ${data.retrieval_intent} · ${data.citations.length} citations · context: ${data.had_context}`,
      });
      setTrail(data.traversal_graph);
      setCitations(data.citations);
    } catch (e) {
      push({ id: uid(), type: "system", text: String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function handleProvision() {
    push({ id: uid(), type: "system", text: `Provisioning ${employeeId}…`, tone: "info" });
    setBusy(true);
    try {
      const data = await api.provision(employeeId);
      push({ id: uid(), type: "provisioning", text: data.narration });
      notify("Provisioning complete");
    } catch (e) {
      push({ id: uid(), type: "system", text: String(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function handleIngest(source: string) {
    const valid = ["github", "slack", "linear", "confluence", "all"];
    const src = source.trim() || "all";
    if (!valid.includes(src)) {
      push({ id: uid(), type: "system", text: `Unknown source '${src}'`, tone: "error" });
      return;
    }
    const sources = src === "all" ? valid.slice(0, 4) : [src];
    setBusy(true);
    for (const s of sources) {
      try {
        await api.ingest(s);
        push({ id: uid(), type: "system", text: `Queued ingestion: ${s}`, tone: "success" });
      } catch (e) {
        push({ id: uid(), type: "system", text: `${s}: ${e}`, tone: "error" });
      }
    }
    setBusy(false);
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (busy) return;
    if (mode === "ask") {
      const q = input.trim() || persona?.sample_query;
      if (!q) return;
      setInput("");
      await handleAsk(q);
    } else if (mode === "provision") {
      setInput("");
      await handleProvision();
    } else {
      const src = input.trim() || "all";
      setInput("");
      await handleIngest(src);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
              <circle cx="11" cy="11" r="9" stroke="currentColor" strokeWidth="1.5" />
              <path d="M11 6v5l3.5 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>
          <div>
            <div className="brand-name">Sentinel</div>
            <div className="brand-sub">Day One</div>
          </div>
        </div>
        <div className="topbar-actions">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const r = await api.seedDrill(true);
                notify(`Demo data loaded (${r.nodes_upserted} nodes)`);
              } catch (err) {
                notify(String(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            Load demo data
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const r = await api.buddyDigest(7, true);
                notify(`Buddy digest: ${r.hires_processed} hires processed`);
              } catch (err) {
                notify(String(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            Buddy digest
          </button>
        </div>
        <div className={`status-pill ${online ? "online" : ""}`}>
          <span className="status-dot" />
          <span>{online ? `Online · ${catalog}` : "Offline"}</span>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <section className="panel">
            <h2 className="panel-title">New hire</h2>
            <ul className="persona-list">
              {personas.map((p) => (
                <li key={p.employee_id}>
                  <button
                    type="button"
                    className={`persona-card ${p.employee_id === employeeId ? "active" : ""}`}
                    onClick={() => setEmployeeId(p.employee_id)}
                  >
                    <span className="avatar">{initials(p.full_name)}</span>
                    <span className="persona-body">
                      <span className="persona-name">{p.full_name}</span>
                      <span className="persona-meta">{p.role}</span>
                      <span className="persona-team">{p.team}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel">
            <h2 className="panel-title">Workflow</h2>
            <div className="seg-control" role="tablist">
              {(["ask", "provision", "ingest"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="tab"
                  aria-selected={mode === m}
                  className={`seg ${mode === m ? "active" : ""}`}
                  onClick={() => setMode(m)}
                >
                  {m.charAt(0).toUpperCase() + m.slice(1)}
                </button>
              ))}
            </div>
          </section>

          <section className="panel panel-grow">
            <h2 className="panel-title">Sample questions</h2>
            <p className="panel-hint">Click to prefill the composer.</p>
            <ul className="drill-list">
              {drill.map((q) => (
                <li key={q.id}>
                  <button
                    type="button"
                    className="drill-item"
                    title={q.hint}
                    onClick={() => {
                      setMode("ask");
                      setInput(q.query);
                    }}
                  >
                    {q.query}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>

        <main className="workspace">
          <div className="workspace-header">
            <div>
              <h1>{copy.title}</h1>
              <p className="workspace-sub">{copy.sub}</p>
            </div>
            {persona && (
              <div className="hire-chip">
                <span className="avatar sm">{initials(persona.full_name)}</span>
                {persona.full_name}
              </div>
            )}
          </div>

          <div className="feed">
            {!hasFeed && (
              <div className="empty-state">
                <h3>Ready when you are</h3>
                <p>Load demo data if the graph is empty, then ask or provision for {persona?.full_name}.</p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    setMode("ask");
                    setInput(persona?.sample_query ?? "");
                  }}
                >
                  Try a sample question
                </button>
              </div>
            )}
            {feed.map((item) => (
              <FeedBubble key={item.id} item={item} />
            ))}
            {busy && (
              <div className="msg assistant loading">
                <div className="msg-bubble">
                  <span className="typing"><span /><span /><span /></span>
                </div>
              </div>
            )}
            <div ref={feedEnd} />
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              value={input}
              rows={1}
              placeholder={copy.placeholder}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSubmit();
                }
              }}
            />
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {mode === "ask" ? "Ask" : mode === "provision" ? "Provision" : "Queue"}
            </button>
          </form>
        </main>

        <aside className="context-panel">
          <section className="panel">
            <h2 className="panel-title">Decision trail</h2>
            <p className="panel-hint">Retrieval path for the latest answer.</p>
          </section>
          <DecisionTrail graph={trail} citations={citations} />
        </aside>
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function FeedBubble({ item }: { item: FeedItem }) {
  if (item.type === "user") {
    return (
      <div className="msg user">
        <div className="msg-meta">{item.employeeId}</div>
        <div className="msg-bubble">{item.text}</div>
      </div>
    );
  }
  if (item.type === "assistant" || item.type === "provisioning") {
    return (
      <div className="msg assistant">
        <div className="msg-bubble">
          {item.text.split("\n").map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
        {"meta" in item && item.meta && <div className="msg-meta">{item.meta}</div>}
      </div>
    );
  }
  return (
    <div className={`msg system tone-${item.tone ?? "info"}`}>
      <div className="msg-bubble">{item.text}</div>
    </div>
  );
}
