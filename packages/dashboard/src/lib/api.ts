// When served from the local server (same origin), use relative URLs.
// When running via `npm run dev` on a different port, set NEXT_PUBLIC_API_URL.
const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "")
    : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000");

export interface ThreadSummary {
  thread_id: string;
  agent_id: string;
  agent_name: string;
  agents: string[];
  trace_id: string;
  status: string;
  step_index: number;
  started_at: string;
  updated_at: string;
  duration_ms: number | null;
  tokens_used: number;
  model: string;
  trigger: string;
}

export interface TimelineEvent {
  ts: string;
  agent_id: string;
  trace_id: string;
  parent_trace_id: string | null;
  type: "user_message" | "assistant_message" | "tool_call" | "tool_result" | "thinking";
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string | null;
  target_agent?: string | null;
  tokens_used?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  duration_ms?: number | null;
}

export interface AgentStats {
  agent_id: string;
  total_runs: number;
  completed_runs: number;
  error_runs: number;
  running_runs: number;
  total_tokens: number;
  models: string[];
  last_seen: string;
  last_status: string;
}

export interface ApiStatus {
  connected: boolean;
  latencyMs?: number;
}

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...opts,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

export async function checkApiStatus(): Promise<ApiStatus> {
  const start = Date.now();
  try {
    await fetch(`${API_BASE}/health`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    return { connected: true, latencyMs: Date.now() - start };
  } catch {
    return { connected: false };
  }
}

export async function listThreads(status?: string): Promise<ThreadSummary[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<ThreadSummary[]>(`/threads${qs}`);
}

export async function getThreadTimeline(threadId: string): Promise<TimelineEvent[]> {
  return apiFetch<TimelineEvent[]>(`/threads/${encodeURIComponent(threadId)}/timeline`);
}

export async function listAgents(): Promise<AgentStats[]> {
  return apiFetch<AgentStats[]>("/agents");
}
