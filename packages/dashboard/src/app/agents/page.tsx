"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import Link from "next/link";
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

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await listAgents({ limit: 100, offset: 0 });
      setAgents(data.items ?? []);
      setTotal(data.total ?? 0);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

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

  // Sort by last_seen descending
  const sorted = useMemo(() =>
    [...filtered].sort((a, b) => new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime()),
  [filtered]);

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
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "var(--text)", letterSpacing: "-0.03em", lineHeight: 1.2 }}>
                Agents
              </h1>
              <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-muted)" }}>
                {total} agent{total !== 1 ? "s" : ""}
              </p>
            </div>
            {/* Search */}
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
                  width: 220, height: 32, padding: "0 30px",
                  borderRadius: 6, border: "1px solid var(--border)",
                  background: "var(--bg-surface)", color: "var(--text)",
                  fontSize: 12.5, fontFamily: "inherit",
                }}
              />
              {search && (
                <button
                  onClick={() => setSearch("")}
                  className="absolute cursor-pointer inline-flex items-center justify-center"
                  style={{
                    right: 6, top: "50%", transform: "translateY(-50%)",
                    width: 18, height: 18, borderRadius: 4,
                    background: "transparent", border: "none",
                    color: "var(--text-muted)", fontSize: 14,
                  }}
                >
                  &times;
                </button>
              )}
            </div>
          </div>
        </header>

        {/* Content */}
        <div style={{ padding: "16px 32px" }}>
          {loading && (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 64, borderRadius: 8 }} />
              ))}
            </div>
          )}

          {!loading && error && (
            <div style={{ padding: 24, borderRadius: 8, background: "var(--red-dim)", border: "1px solid rgba(239,68,68,0.15)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--red)", marginBottom: 6 }}>Cannot reach API</div>
              <div style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{error}</div>
            </div>
          )}

          {!loading && !error && sorted.length === 0 && (
            <div className="flex flex-col items-center justify-center" style={{ padding: "80px 0", color: "var(--text-muted)" }}>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>
                {search ? "No agents match" : "No agents yet"}
              </div>
              <div style={{ fontSize: 12.5 }}>
                {search ? "Try clearing your search." : "Run your first agent to see it here."}
              </div>
            </div>
          )}

          {!loading && !error && sorted.length > 0 && (
            <div className="flex flex-col gap-2">
              {sorted.map((agent) => {
                const isExpanded = expandedId === agent.agent_id;
                const successRate = agent.total_runs > 0 ? Math.round((agent.completed_runs / agent.total_runs) * 100) : 0;
                const rateColor = successRate >= 90 ? "var(--green)" : successRate >= 70 ? "var(--amber)" : "var(--red)";
                const isRunning = normalizeStatus(agent.last_status) === "running";

                return (
                  <div
                    key={agent.agent_id}
                    style={{
                      background: "var(--bg-surface)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      overflow: "hidden",
                    }}
                  >
                    {/* Main row */}
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : agent.agent_id)}
                      className="flex items-center w-full cursor-pointer"
                      style={{ padding: "12px 16px", background: "transparent", border: "none", textAlign: "left" }}
                    >
                      {/* Status dot */}
                      <span
                        className="shrink-0"
                        style={{
                          width: 8, height: 8, borderRadius: "50%", marginRight: 12,
                          background: isRunning ? "var(--green)" : normalizeStatus(agent.last_status) === "error" ? "var(--red)" : "var(--text-dim)",
                          animation: isRunning ? "pulse-dot 2s ease-in-out infinite" : "none",
                          opacity: isRunning ? 1 : 0.5,
                        }}
                      />

                      {/* Agent name */}
                      <span style={{ fontSize: 13, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)", marginRight: 10, minWidth: 0 }}>
                        {agent.agent_id}
                      </span>

                      {/* Status badge */}
                      <StatusBadge status={agent.last_status} />

                      {/* Models */}
                      <div className="flex items-center gap-1" style={{ marginLeft: 12, marginRight: 10 }}>
                        {agent.models.slice(0, 2).map((m) => (
                          <span
                            key={m}
                            style={{
                              fontSize: 10, padding: "1px 6px", borderRadius: 3,
                              background: "var(--bg-raised)", border: "1px solid var(--border)",
                              color: "var(--text-dim)", whiteSpace: "nowrap",
                            }}
                          >
                            {shortModel(m)}
                          </span>
                        ))}
                        {agent.models.length > 2 && (
                          <span style={{ fontSize: 9, color: "var(--text-dim)" }}>+{agent.models.length - 2}</span>
                        )}
                      </div>

                      {/* Stats — right aligned */}
                      <div className="flex items-center gap-5 ml-auto shrink-0" style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                        <span>{agent.total_runs} run{agent.total_runs !== 1 ? "s" : ""}</span>
                        <span style={{ color: rateColor }}>{successRate}%</span>
                        <span>{formatTokens(agent.total_tokens)}t</span>
                        <span>{formatRelTime(agent.last_seen)}</span>
                        <span style={{ fontSize: 12, color: "var(--text-muted)", transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}>
                          &#x25BC;
                        </span>
                      </div>
                    </button>

                    {/* Expanded details */}
                    {isExpanded && (
                      <div className="animate-fade-in" style={{ borderTop: "1px solid var(--border)", padding: "12px 16px" }}>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 12 }}>
                          <StatMini label="Total Runs" value={String(agent.total_runs)} />
                          <StatMini label="Completed" value={String(agent.completed_runs)} accent="var(--green)" />
                          <StatMini label="Errors" value={String(agent.error_runs)} accent={agent.error_runs > 0 ? "var(--red)" : undefined} />
                          <StatMini label="Running" value={String(agent.running_runs)} accent={agent.running_runs > 0 ? "var(--green)" : undefined} />
                        </div>

                        {/* Success rate bar */}
                        <div style={{ marginBottom: 12 }}>
                          <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
                            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", textTransform: "uppercase" }}>Success Rate</span>
                            <span style={{ fontSize: 12, fontWeight: 600, fontFamily: "var(--font-mono)", color: rateColor }}>{successRate}%</span>
                          </div>
                          <div style={{ height: 4, borderRadius: 2, background: "var(--bg-raised)", overflow: "hidden" }}>
                            <div style={{ height: "100%", width: `${successRate}%`, background: rateColor, borderRadius: 2, transition: "width 0.3s ease" }} />
                          </div>
                        </div>

                        {/* Models list */}
                        {agent.models.length > 0 && (
                          <div>
                            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", textTransform: "uppercase" }}>Models</span>
                            <div className="flex flex-wrap gap-1.5" style={{ marginTop: 6 }}>
                              {agent.models.map((m) => (
                                <span
                                  key={m}
                                  title={m}
                                  style={{
                                    fontSize: 11, padding: "2px 8px", borderRadius: 4,
                                    background: "var(--bg-raised)", border: "1px solid var(--border)",
                                    color: "var(--text-secondary)", fontFamily: "var(--font-mono)",
                                  }}
                                >
                                  {shortModel(m)}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Link to filtered runs */}
                        <div style={{ marginTop: 12 }}>
                          <Link
                            href={`/runs?search=${encodeURIComponent(agent.agent_id)}`}
                            className="no-underline"
                            style={{ fontSize: 12, color: "var(--purple)" }}
                          >
                            View all runs for {agent.agent_id} &rarr;
                          </Link>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function StatMini({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{ padding: "8px 10px", background: "var(--bg-raised)", borderRadius: 6 }}>
      <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontFamily: "var(--font-mono)", fontWeight: 600, color: accent ?? "var(--text)" }}>
        {value}
      </div>
    </div>
  );
}
