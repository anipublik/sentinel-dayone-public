"""Serialize retrieval traversal into a frontend-friendly graph."""

from __future__ import annotations

from typing import Any

from sentinel.retrieval.engine import Citation, RetrievalResult


def _node_id(node: dict[str, Any] | None) -> str | None:
    if not node:
        return None
    return node.get("id") or node.get("global_id") or node.get("full_name")


def _kind_from_node(node: dict[str, Any]) -> str:
    if node.get("global_id") and "#" in str(node.get("global_id", "")):
        return "pr"
    if "channel" in node:
        return "slack"
    if "started_at" in node:
        return "incident"
    if node.get("space_key"):
        return "confluence"
    if node.get("status") and node.get("title") and not node.get("global_id"):
        return "ticket"
    if str(node.get("id", "")).upper().startswith("ADR"):
        return "adr"
    return "unknown"


def _citation_node(c: Citation, role: str) -> dict[str, Any]:
    return {
        "id": c.id,
        "kind": c.kind,
        "title": c.title,
        "role": role,
        "url": c.url,
        "score": c.score,
    }


def _parse_path_edges(path: Any, edges: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    if not path or not isinstance(path, dict):
        return
    for segment in path.get("segments") or []:
        rel = segment.get("relationship") or {}
        rel_type = rel.get("type") if isinstance(rel, dict) else getattr(rel, "type", "RELATED")
        start = segment.get("start")
        end = segment.get("end")
        start_id = _node_id(start if isinstance(start, dict) else None)
        end_id = _node_id(end if isinstance(end, dict) else None)
        if start_id and end_id:
            key = (start_id, end_id, str(rel_type))
            edges[key] = {"from": start_id, "to": end_id, "type": str(rel_type)}


def build_traversal_graph(result: RetrievalResult) -> dict[str, Any]:
    """Build {nodes, edges, intent} for the decision-trail visualizer."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    for c in result.entry_points:
        nodes[c.id] = _citation_node(c, "entry")
    for c in result.connected:
        if c.id not in nodes:
            nodes[c.id] = _citation_node(c, "connected")
        else:
            nodes[c.id]["role"] = "both"

    for row in result.raw_traversal:
        n = row.get("n")
        connected = row.get("connected")
        n_id = _node_id(n if isinstance(n, dict) else None)
        c_id = _node_id(connected if isinstance(connected, dict) else None)

        if n_id and isinstance(n, dict) and n_id not in nodes:
            nodes[n_id] = {
                "id": n_id,
                "kind": _kind_from_node(n),
                "title": n.get("title") or n.get("name") or n_id,
                "role": "traversal",
                "url": n.get("url"),
                "score": 0.0,
            }
        if c_id and isinstance(connected, dict) and c_id not in nodes:
            nodes[c_id] = {
                "id": c_id,
                "kind": _kind_from_node(connected),
                "title": connected.get("title") or connected.get("name") or c_id,
                "role": "traversal",
                "url": connected.get("url"),
                "score": 0.0,
            }
        if n_id and c_id:
            key = (n_id, c_id, "RELATED")
            edges[key] = {"from": n_id, "to": c_id, "type": "RELATED"}

        _parse_path_edges(row.get("path"), edges)

    return {
        "intent": result.intent.value,
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }
