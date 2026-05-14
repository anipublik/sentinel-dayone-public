import type {
  AskResponse,
  DrillQuestion,
  HealthResponse,
  Persona,
  ProvisionResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail ?? res.statusText;
    throw new Error(detail);
  }
  return data as T;
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  personas: () => request<Persona[]>("/api/v1/personas"),
  drillQuestions: () => request<DrillQuestion[]>("/api/v1/drill/questions"),
  ask: (employee_id: string, query: string) =>
    request<AskResponse>("/api/v1/ask", {
      method: "POST",
      body: JSON.stringify({ employee_id, query }),
    }),
  provision: (employee_id: string) =>
    request<ProvisionResponse>("/api/v1/provision", {
      method: "POST",
      body: JSON.stringify({ employee_id }),
    }),
  ingest: (source: string) =>
    request<{ status: string; source: string }>("/api/v1/ingest", {
      method: "POST",
      body: JSON.stringify({ source }),
    }),
  seedDrill: (clear_existing = true) =>
    request<{ nodes_upserted: number; edges_created: number }>("/api/v1/seed/drill", {
      method: "POST",
      body: JSON.stringify({ clear_existing }),
    }),
  buddyDigest: (since_days = 7, dry_run = true) =>
    request<{ hires_processed: number; messages_sent: number; digests: unknown[] }>(
      "/api/v1/buddy-digest",
      { method: "POST", body: JSON.stringify({ since_days, dry_run }) },
    ),
};
