import { useEffect, useState } from "react";
import type { TraversalGraph } from "../types";

const KIND_COLORS: Record<string, string> = {
  entry: "var(--accent)",
  pr: "var(--blue)",
  slack: "var(--purple)",
  adr: "var(--green)",
  ticket: "var(--amber)",
  incident: "var(--red)",
  confluence: "#79c0ff",
  unknown: "var(--muted)",
};

interface Props {
  graph: TraversalGraph | null;
  citations: Array<{ kind: string; id: string; title: string; url: string | null }>;
}

export function DecisionTrail({ graph, citations }: Props) {
  const [size, setSize] = useState({ w: 320, h: 280 });

  useEffect(() => {
    const el = document.getElementById("trail-svg-wrap");
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ w: Math.max(width, 200), h: Math.max(height, 200) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!graph?.nodes?.length) {
    return (
      <div className="trail-panel">
        <div className="trail-empty">
          <p>No retrieval graph yet.</p>
          <p className="muted">Ask a question to see how evidence was connected.</p>
        </div>
        <CitationChips items={citations} />
      </div>
    );
  }

  const entry = graph.nodes.filter((n) => n.role === "entry" || n.role === "both");
  const other = graph.nodes.filter((n) => n.role !== "entry" && n.role !== "both");
  const leftX = size.w * 0.2;
  const rightX = size.w * 0.8;
  const pad = 40;
  const positions = new Map<string, { x: number; y: number; node: (typeof graph.nodes)[0] }>();

  entry.forEach((n, i) => {
    const y = pad + ((i + 1) / (entry.length + 1)) * (size.h - pad * 2);
    positions.set(n.id, { x: leftX, y, node: n });
  });
  other.forEach((n, i) => {
    const y = pad + ((i + 1) / (other.length + 1)) * (size.h - pad * 2);
    positions.set(n.id, { x: rightX, y, node: n });
  });

  return (
    <div className="trail-panel">
      <div className="trail-meta">
        <span className="badge">Intent: {graph.intent}</span>
        <span className="muted">{graph.nodes.length} nodes · {graph.edges.length} edges</span>
      </div>
      <div className="trail-svg-wrap" id="trail-svg-wrap">
        <svg width={size.w} height={size.h} className="trail-svg">
          {graph.edges.map((e, i) => {
            const a = positions.get(e.from);
            const b = positions.get(e.to);
            if (!a || !b) return null;
            const mx = (a.x + b.x) / 2;
            return (
              <path
                key={`${e.from}-${e.to}-${i}`}
                d={`M ${a.x + 12} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - 12} ${b.y}`}
                fill="none"
                stroke="var(--border-strong)"
                strokeWidth="1.5"
                opacity="0.7"
              />
            );
          })}
          {[...positions.values()].map(({ x, y, node }) => (
            <g key={node.id} transform={`translate(${x}, ${y})`}>
              <circle
                r="10"
                fill={KIND_COLORS[node.role === "entry" ? "entry" : node.kind] ?? KIND_COLORS.unknown}
                stroke="var(--surface-elevated)"
                strokeWidth="2"
              />
              <text x="-8" y="26" className="trail-label" textAnchor="middle">
                {(node.title || node.id).slice(0, 22)}
              </text>
              <text x="-8" y="38" className="trail-kind" textAnchor="middle">
                {node.kind}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <CitationChips items={citations} />
    </div>
  );
}

function CitationChips({
  items,
}: {
  items: Array<{ kind: string; id: string; title: string; url: string | null }>;
}) {
  if (!items.length) return null;
  return (
    <div className="citation-list">
      <h3 className="citation-heading">Citations</h3>
      {items.map((c) => (
        <a
          key={c.id}
          className="citation-chip"
          href={c.url ?? undefined}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => !c.url && e.preventDefault()}
        >
          <span className={`kind-dot kind-${c.kind}`} />
          <span className="citation-title">{c.title || c.id}</span>
          <span className="citation-id">{c.id}</span>
        </a>
      ))}
    </div>
  );
}
