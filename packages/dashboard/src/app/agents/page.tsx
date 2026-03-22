"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  listAgents,
  listChannels,
  checkApiStatus,
  type AgentStats,
  type ApiStatus,
  type Channel,
} from "@/lib/api";
import Sidebar from "@/components/sidebar";
import StatusBadge from "@/components/status-badge";
import { formatTokens, formatRelTime, shortModel, normalizeStatus } from "@/lib/utils";

/* ── Constants ───────────────────────────────────────────────────────────── */

const PAGE_SIZE = 100;

type SortKey = "agent_id" | "total_runs" | "total_tokens" | "last_seen" | "success_rate";

function sortAgents(agents: AgentStats[], key: SortKey, dir: "asc" | "desc"): AgentStats[] {
  return [...agents].sort((a, b) => {
    let av: number | string;
    let bv: number | string;
    if (key === "success_rate") {
      av = a.total_runs ? a.completed_runs / a.total_runs : 0;
      bv = b.total_runs ? b.completed_runs / b.total_runs : 0;
    } else if (key === "last_seen") {
      av = new Date(a.last_seen).getTime();
      bv = new Date(b.last_seen).getTime();
    } else if (key === "agent_id") {
      av = a.agent_id;
      bv = b.agent_id;
    } else {
      av = a[key] as number;
      bv = b[key] as number;
    }
    if (av < bv) return dir === "asc" ? -1 : 1;
    if (av > bv) return dir === "asc" ? 1 : -1;
    return 0;
  });
}

/* ── Success rate bar ────────────────────────────────────────────────────── */

function SuccessRate({ completed, total, errors }: { completed: number; total: number; errors: number }) {
  if (total === 0) return <span style={{ color: "var(--text-dim)", fontSize: 12 }}>--</span>;
  const rate = Math.round((completed / total) * 100);
  const color = rate >= 90 ? "var(--green)" : rate >= 70 ? "var(--amber)" : "var(--red)";
  return (
    <div className="flex flex-col gap-1" style={{ minWidth: 72 }}>
      <div className="flex items-center justify-between">
        <span style={{ fontSize: 12, fontWeight: 600, color, fontFamily: "var(--font-mono)" }}>
          {rate}%
        </span>
        {errors > 0 && (
          <span style={{ fontSize: 10, color: "var(--red)", opacity: 0.7 }}>
            {errors} err
          </span>
        )}
      </div>
      <div
        style={{
          height: 3,
          background: "var(--bg-raised)",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${rate}%`,
            background: color,
            borderRadius: 2,
            transition: "width 0.4s ease",
          }}
        />
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("last_seen");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [channels, setChannels] = useState<Channel[]>([]);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await listAgents({ limit: PAGE_SIZE, offset });
      setAgents(data.items ?? []);
      setTotal(data.total ?? 0);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  const fetchStatus = useCallback(async () => {
    setApiStatus(await checkApiStatus());
  }, []);

  const fetchChannels = useCallback(async () => {
    try { setChannels(await listChannels()); }
    catch { /* channels may not be available */ }
  }, []);

  useEffect(() => { fetchAgents(); fetchStatus(); fetchChannels(); }, [fetchAgents, fetchStatus, fetchChannels]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) {
      intervalRef.current = setInterval(() => { fetchAgents(); fetchStatus(); fetchChannels(); }, 5000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, fetchAgents, fetchStatus, fetchChannels]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) => a.agent_id.toLowerCase().includes(q) || a.models.some((m) => m.toLowerCase().includes(q)),
    );
  }, [agents, search]);

  const sorted = useMemo(() => sortAgents(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  const columns: { key: SortKey | null; label: string; align?: "right" }[] = [
    { key: "agent_id",     label: "AGENT" },
    { key: null,           label: "STATUS" },
    { key: "total_runs",   label: "RUNS",         align: "right" },
    { key: "success_rate", label: "SUCCESS RATE" },
    { key: "total_tokens", label: "TOKENS",        align: "right" },
    { key: null,           label: "MODELS" },
    { key: "last_seen",    label: "LAST ACTIVE",   align: "right" },
  ];

  return (
    <div className="min-h-screen" style={{ background: "var(--bg)" }}>
      <Sidebar
        agents={agents}
        channels={channels}
        apiStatus={apiStatus}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        onChannelsChanged={fetchChannels}
      />

      <main style={{ marginLeft: "var(--sidebar-w)" }}>
        {/* Header */}
        <header
          className="animate-fade-in"
          style={{ padding: "28px 32px 20px", borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-start justify-between">
            <div>
              <h1
                style={{
                  margin: 0, fontSize: 22, fontWeight: 700,
                  color: "var(--text)", letterSpacing: "-0.03em", lineHeight: 1.2,
                }}
              >
                Agents
              </h1>
              <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-muted)" }}>
                {total} agents &middot; local mode
              </p>
            </div>
            <div className="relative">
              <svg
                className="absolute pointer-events-none"
                style={{ left: 10, top: "50%", transform: "translateY(-50%)", opacity: 0.3 }}
                width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              >
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.3-4.3" />
              </svg>
              <input
                type="text"
                placeholder="Search agents..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="outline-none"
                style={{
                  width: 220, height: 32, padding: "0 10px 0 30px",
                  borderRadius: 6, border: "1px solid var(--border)",
                  background: "var(--bg-surface)", color: "var(--text)",
                  fontSize: 12.5, fontFamily: "inherit",
                }}
              />
            </div>
          </div>
        </header>

        {/* Table */}
        <div>
          {loading && (
            <div style={{ padding: "16px 32px" }} className="flex flex-col gap-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 44, borderRadius: 6 }} />
              ))}
            </div>
          )}

          {!loading && error && (
            <div style={{ padding: 32 }}>
              <div
                style={{
                  padding: 24, borderRadius: 8,
                  background: "var(--red-dim)", border: "1px solid rgba(239,68,68,0.15)",
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--red)", marginBottom: 6 }}>
                  Cannot reach API
                </div>
                <div style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)", marginBottom: 10 }}>
                  {error}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Run <code style={{ background: "var(--bg-raised)", padding: "1px 6px", borderRadius: 4, fontFamily: "var(--font-mono)" }}>ninetrix dev</code> to start the local stack
                </div>
              </div>
            </div>
          )}

          {!loading && !error && sorted.length === 0 && (
            <div className="flex flex-col items-center justify-center" style={{ padding: "80px 32px", color: "var(--text-muted)" }}>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>
                {search ? "No agents match" : "No agents yet"}
              </div>
              <div style={{ fontSize: 12.5 }}>
                {search ? "Try clearing your search." : "Run your first agent to see it here."}
              </div>
            </div>
          )}

          {!loading && !error && sorted.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th
                      key={col.label}
                      onClick={col.key ? () => handleSort(col.key!) : undefined}
                      style={{
                        padding: "0 16px", height: 36,
                        fontSize: 10.5, fontWeight: 500,
                        color: col.key && sortKey === col.key ? "var(--text-secondary)" : "var(--text-dim)",
                        textAlign: col.align ?? "left",
                        letterSpacing: "0.06em", textTransform: "uppercase",
                        borderBottom: "1px solid var(--border)",
                        background: "var(--bg)",
                        cursor: col.key ? "pointer" : "default",
                        userSelect: "none", whiteSpace: "nowrap",
                      }}
                    >
                      {col.label}
                      {col.key && sortKey === col.key && (
                        <span style={{ fontSize: 9, color: "var(--green)", marginLeft: 4 }}>
                          {sortDir === "asc" ? "\u2191" : "\u2193"}
                        </span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((agent) => (
                  <tr
                    key={agent.agent_id}
                    className="transition-colors"
                    style={{ borderBottom: "1px solid var(--border)" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    {/* Agent ID */}
                    <td style={{ padding: "10px 16px" }}>
                      <span style={{
                        fontSize: 13, fontWeight: 500, color: "var(--text)",
                        fontFamily: "var(--font-mono)", letterSpacing: "-0.01em",
                      }}>
                        {agent.agent_id}
                      </span>
                    </td>

                    {/* Status */}
                    <td style={{ padding: "10px 16px" }}>
                      <StatusBadge status={agent.last_status} />
                    </td>

                    {/* Runs */}
                    <td style={{ padding: "10px 16px", textAlign: "right" }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", fontFamily: "var(--font-mono)" }}>
                        {agent.total_runs}
                      </span>
                    </td>

                    {/* Success rate */}
                    <td style={{ padding: "10px 16px" }}>
                      <SuccessRate completed={agent.completed_runs} total={agent.total_runs} errors={agent.error_runs} />
                    </td>

                    {/* Tokens */}
                    <td style={{ padding: "10px 16px", textAlign: "right" }}>
                      <span style={{ fontSize: 12.5, color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                        {formatTokens(agent.total_tokens)}
                      </span>
                    </td>

                    {/* Models */}
                    <td style={{ padding: "10px 16px" }}>
                      <div className="flex flex-wrap gap-1">
                        {agent.models.slice(0, 3).map((m) => (
                          <span
                            key={m}
                            title={m}
                            style={{
                              display: "inline-flex", padding: "1px 7px",
                              borderRadius: 4, background: "var(--bg-raised)",
                              border: "1px solid var(--border)",
                              color: "var(--text-secondary)", fontSize: 11,
                              fontWeight: 500, whiteSpace: "nowrap",
                            }}
                          >
                            {shortModel(m)}
                          </span>
                        ))}
                        {agent.models.length > 3 && (
                          <span style={{ padding: "1px 6px", borderRadius: 4, background: "var(--bg-raised)", color: "var(--text-dim)", fontSize: 10 }}>
                            +{agent.models.length - 3}
                          </span>
                        )}
                        {agent.models.length === 0 && (
                          <span style={{ color: "var(--text-dim)", fontSize: 12 }}>--</span>
                        )}
                      </div>
                    </td>

                    {/* Last active */}
                    <td style={{ padding: "10px 16px", textAlign: "right" }}>
                      <span
                        title={new Date(agent.last_seen).toLocaleString()}
                        style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}
                      >
                        {formatRelTime(agent.last_seen)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* Pagination */}
          {!loading && !error && Math.ceil(total / PAGE_SIZE) > 1 && (
            <div
              className="flex items-center justify-between"
              style={{ padding: "10px 32px", borderTop: "1px solid var(--border)" }}
            >
              <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                {offset + 1}&ndash;{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                {[
                  { label: "\u00AB", p: 0, disabled: offset === 0 },
                  { label: "\u2039", p: offset - PAGE_SIZE, disabled: offset === 0 },
                  { label: "\u203A", p: offset + PAGE_SIZE, disabled: offset + PAGE_SIZE >= total },
                  { label: "\u00BB", p: (Math.ceil(total / PAGE_SIZE) - 1) * PAGE_SIZE, disabled: offset + PAGE_SIZE >= total },
                ].map((btn, i) => (
                  <button
                    key={i}
                    disabled={btn.disabled}
                    onClick={() => { setOffset(Math.max(0, btn.p)); setLoading(true); }}
                    className="flex items-center justify-center cursor-pointer"
                    style={{
                      width: 28, height: 28, borderRadius: 5,
                      border: "1px solid var(--border)", background: "transparent",
                      color: btn.disabled ? "var(--text-dim)" : "var(--text-secondary)",
                      fontSize: 14, opacity: btn.disabled ? 0.3 : 1,
                    }}
                  >
                    {btn.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
