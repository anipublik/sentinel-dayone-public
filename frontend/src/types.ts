export type WorkflowMode = "ask" | "provision" | "ingest";

export interface Persona {
  employee_id: string;
  full_name: string;
  team: string;
  role: string;
  buddy_id?: string | null;
  sample_query: string;
}

export interface Citation {
  kind: string;
  id: string;
  title: string;
  url: string | null;
  score?: number;
  excerpt?: string;
}

export interface TraversalGraph {
  intent: string;
  nodes: Array<{
    id: string;
    kind: string;
    title: string;
    role: string;
    url?: string | null;
    score?: number;
  }>;
  edges: Array<{ from: string; to: string; type: string }>;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  retrieval_intent: string;
  had_context: boolean;
  traversal_graph: TraversalGraph;
}

export interface ProvisionResponse {
  narration: string;
  report: Record<string, unknown>;
  topology: Record<string, unknown>;
}

export interface DrillQuestion {
  id: string;
  query: string;
  intent: string;
  hint: string;
}

export interface HealthResponse {
  status: string;
  uptime_seconds: number;
  catalog: string | null;
}

export type FeedItem =
  | { id: string; type: "user"; text: string; employeeId: string }
  | { id: string; type: "assistant"; text: string; meta?: string }
  | { id: string; type: "system"; text: string; tone?: "info" | "success" | "error" | "warn" }
  | { id: string; type: "provisioning"; text: string };
