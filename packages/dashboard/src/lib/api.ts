const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

/* ── Types ───────────────────────────────────────────────────────────────── */

export interface ApprovalItem {
  trace_id: string;
  thread_id: string;
  agent_id: string;
  step_index: number;
  pending_tool_calls: Array<{ name?: string; arguments?: string; [key: string]: unknown }>;
  created_at: string;
}

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
  last_heartbeat: string | null;
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

export interface StreamUpdate {
  type: string;
  thread_id: string;
  status: string;
  step_index: number;
  events: TimelineEvent[];
}

export interface Channel {
  id: string;
  channel_type: string;
  name: string;
  config: Record<string, string>;
  session_mode: string;
  routing_mode: string;
  verified: boolean;
  enabled: boolean;
  created_at: string;
  agents?: ChannelBinding[];
}

export interface ChannelBinding {
  id: string;
  agent_name: string;
  is_default: boolean;
  command: string | null;
}

/* ── Fetcher ─────────────────────────────────────────────────────────────── */

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...opts });
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

/* ── Auth ────────────────────────────────────────────────────────────────── */

let _cachedToken: string | null = null;

async function getAuthToken(): Promise<string> {
  if (_cachedToken) return _cachedToken;
  const res = await apiFetch<{ token: string }>("/internal/auth/token");
  _cachedToken = res.token;
  return _cachedToken;
}

async function authFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = await getAuthToken();
  const headers = new Headers(opts?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");
  return apiFetch<T>(path, { ...opts, headers });
}

/* ── Endpoints ───────────────────────────────────────────────────────────── */

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

export function subscribeThreadStream(
  threadId: string,
  onUpdate: (update: StreamUpdate) => void,
  onDone: () => void,
): () => void {
  const url = `${API_BASE}/threads/${encodeURIComponent(threadId)}/stream`;
  const es = new EventSource(url);

  es.onmessage = (e) => {
    try { onUpdate(JSON.parse(e.data) as StreamUpdate); }
    catch { /* ignore malformed frames */ }
  };

  es.addEventListener("done", () => { onDone(); es.close(); });
  es.addEventListener("error", () => { es.close(); });
  es.onerror = () => { onDone(); es.close(); };

  return () => es.close();
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

/* ── Approvals ──────────────────────────────────────────────────────────── */

export async function listApprovals(): Promise<ApprovalItem[]> {
  return apiFetch<ApprovalItem[]>("/approvals");
}

export async function approveAction(
  traceId: string,
  stepIndex: number,
): Promise<{ ok: boolean }> {
  return apiFetch(`/approvals/${encodeURIComponent(traceId)}/${stepIndex}/approve`, {
    method: "POST",
  });
}

export async function rejectAction(
  traceId: string,
  stepIndex: number,
): Promise<{ ok: boolean }> {
  return apiFetch(`/approvals/${encodeURIComponent(traceId)}/${stepIndex}/reject`, {
    method: "POST",
  });
}

/* ── Channels ───────────────────────────────────────────────────────────── */

export async function listChannels(): Promise<Channel[]> {
  return authFetch<Channel[]>("/v1/channels");
}

export async function createChannel(
  channelType: string,
  name: string,
  config: Record<string, string>,
): Promise<Channel> {
  return authFetch<Channel>("/v1/channels", {
    method: "POST",
    body: JSON.stringify({ channel_type: channelType, name, config }),
  });
}

export async function getChannel(channelId: string): Promise<Channel> {
  return authFetch<Channel>(`/v1/channels/${encodeURIComponent(channelId)}`);
}

export async function updateChannel(
  channelId: string,
  patch: { name?: string; session_mode?: string; routing_mode?: string; enabled?: boolean },
): Promise<Channel> {
  return authFetch<Channel>(`/v1/channels/${encodeURIComponent(channelId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteChannel(channelId: string): Promise<void> {
  await authFetch<void>(`/v1/channels/${encodeURIComponent(channelId)}`, {
    method: "DELETE",
  });
}

export async function verifyChannel(
  channelId: string,
  code: string,
): Promise<{ status: string }> {
  return authFetch<{ status: string }>(`/v1/channels/${encodeURIComponent(channelId)}/verify`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export async function bindAgent(
  channelId: string,
  agentName: string,
  isDefault = true,
): Promise<ChannelBinding> {
  return authFetch<ChannelBinding>(`/v1/channels/${encodeURIComponent(channelId)}/agents`, {
    method: "POST",
    body: JSON.stringify({ agent_name: agentName, is_default: isDefault }),
  });
}

export async function unbindAgent(
  channelId: string,
  agentName: string,
): Promise<void> {
  await authFetch<void>(
    `/v1/channels/${encodeURIComponent(channelId)}/agents/${encodeURIComponent(agentName)}`,
    { method: "DELETE" },
  );
}
