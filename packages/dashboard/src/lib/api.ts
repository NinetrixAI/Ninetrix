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
  run_cost_usd: number;
  budget_usd: number;
  budget_soft_warned: boolean;
  rate_limited: boolean;
  rate_limit_waits: number;
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

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
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

export async function listThreads(opts?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<Page<ThreadSummary>> {
  const params = new URLSearchParams({ sort: "started_at", order: "desc" });
  if (opts?.status) params.set("status", opts.status);
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  return apiFetch<Page<ThreadSummary>>(`/threads?${params}`);
}

export async function getThreadTimeline(threadId: string): Promise<TimelineEvent[]> {
  return apiFetch<TimelineEvent[]>(`/threads/${encodeURIComponent(threadId)}/timeline`);
}

export async function listAgents(opts?: {
  limit?: number;
  offset?: number;
}): Promise<Page<AgentStats>> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return apiFetch<Page<AgentStats>>(`/agents${qs ? "?" + qs : ""}`);
}
