"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  listThreads,
  listAgents,
  listApprovals,
  listChannels,
  getThreadTimeline,
  checkApiStatus,
  subscribeThreadStream,
  type ThreadSummary,
  type AgentStats,
  type ApiStatus,
  type ApprovalItem,
  type Channel,
  type StreamUpdate,
} from "@/lib/api";
import { timelineEventsToTraceNodes, type TraceNode } from "@/lib/trace";
import Sidebar from "@/components/sidebar";
import StatusBadge from "@/components/status-badge";
import TracePanel from "@/components/trace-panel";
import {
  formatDuration,
  formatTokens,
  formatRelTime,
  formatCost,
  shortModel,
  normalizeStatus,
} from "@/lib/utils";
import { BudgetInline } from "@/components/budget-meter";
import ApprovalDialog from "@/components/approval-dialog";

/* ── Constants ───────────────────────────────────────────────────────────── */

const PAGE_SIZE = 50;

const STATUS_TABS = [
  { key: "all",       label: "All" },
  { key: "running",   label: "Running" },
  { key: "completed", label: "Completed" },
  { key: "error",     label: "Failed" },
  { key: "pending",   label: "Pending" },
];

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function RunsPage() {
  /* ── State ─────────────────────────────────────────────────────────────── */
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [traceNodes, setTraceNodes] = useState<TraceNode[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);

  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [showApprovalDialog, setShowApprovalDialog] = useState(false);

  const [channels, setChannels] = useState<Channel[]>([]);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sseCleanupRef = useRef<(() => void) | null>(null);

  /* ── Data fetching ─────────────────────────────────────────────────────── */
  const fetchThreads = useCallback(async () => {
    try {
      const data = await listThreads({ limit: PAGE_SIZE, offset });
      setThreads(data.items ?? []);
      setTotal(data.total ?? 0);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await listAgents({ limit: 100 });
      setAgents(data.items ?? []);
    } catch { /* sidebar agents are non-critical */ }
  }, []);

  const fetchStatus = useCallback(async () => {
    setApiStatus(await checkApiStatus());
  }, []);

  const fetchApprovals = useCallback(async () => {
    try {
      const items = await listApprovals();
      setApprovals(items);
    } catch { /* approvals endpoint may not exist on older APIs */ }
  }, []);

  const fetchChannels = useCallback(async () => {
    try {
      const items = await listChannels();
      setChannels(items);
    } catch { /* channels may not be available */ }
  }, []);

  useEffect(() => {
    fetchThreads();
    fetchAgents();
    fetchStatus();
    fetchApprovals();
    fetchChannels();
  }, [fetchThreads, fetchAgents, fetchStatus, fetchApprovals, fetchChannels]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) {
      intervalRef.current = setInterval(() => {
        fetchThreads();
        fetchAgents();
        fetchStatus();
        fetchApprovals();
        fetchChannels();
      }, 5000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, fetchThreads, fetchAgents, fetchStatus, fetchApprovals, fetchChannels]);

  /* ── Trace loading ─────────────────────────────────────────────────────── */
  useEffect(() => {
    if (sseCleanupRef.current) { sseCleanupRef.current(); sseCleanupRef.current = null; }
    if (!selectedId) { setTraceNodes([]); return; }

    const thread = threads.find((t) => t.thread_id === selectedId);
    if (!thread) return;

    setTraceLoading(true);
    getThreadTimeline(selectedId)
      .then((events) => {
        const nodes = timelineEventsToTraceNodes(events, thread);
        setTraceNodes(nodes);
        setTraceLoading(false);

        // SSE for live threads
        const isLive = thread.status === "in_progress" || thread.status === "running" || thread.status === "started" || thread.status === "idle";
        if (isLive) {
          let accEvents = [...events];
          let sseFirstBatch = true;
          const cleanup = subscribeThreadStream(
            selectedId,
            (update: StreamUpdate) => {
              if (update.events?.length) {
                if (sseFirstBatch) {
                  // First SSE batch contains full history — replace to avoid duplication
                  accEvents = update.events;
                  sseFirstBatch = false;
                } else {
                  accEvents = [...accEvents, ...update.events];
                }
                const latestThread = { ...thread, status: update.status, step_index: update.step_index };
                setTraceNodes(timelineEventsToTraceNodes(accEvents, latestThread));
              }
            },
            () => {
              // Refresh on completion
              fetchThreads();
              getThreadTimeline(selectedId).then((ev) => {
                const t = threads.find((x) => x.thread_id === selectedId);
                if (t) setTraceNodes(timelineEventsToTraceNodes(ev, t));
              });
            },
          );
          sseCleanupRef.current = cleanup;
        }
      })
      .catch(() => setTraceLoading(false));

    return () => {
      if (sseCleanupRef.current) { sseCleanupRef.current(); sseCleanupRef.current = null; }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  /* ── Filtering ─────────────────────────────────────────────────────────── */
  const filtered = useMemo(() => {
    return threads.filter((t) => {
      const q = search.toLowerCase();
      const matchSearch = !q ||
        t.thread_id.toLowerCase().includes(q) ||
        t.agent_id.toLowerCase().includes(q) ||
        (t.agent_name ?? "").toLowerCase().includes(q) ||
        t.model.toLowerCase().includes(q);
      const norm = normalizeStatus(t.status);
      const matchStatus = statusFilter === "all" || norm === statusFilter;
      return matchSearch && matchStatus;
    });
  }, [threads, search, statusFilter]);

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = { all: threads.length };
    for (const t of threads) {
      const n = normalizeStatus(t.status);
      counts[n] = (counts[n] ?? 0) + 1;
    }
    return counts;
  }, [threads]);

  /* ── Stats ─────────────────────────────────────────────────────────────── */
  const stats = useMemo(() => {
    const totalRuns = total;
    const completed = threads.filter((t) => normalizeStatus(t.status) === "completed").length;
    const successRate = threads.length > 0 ? ((completed / threads.length) * 100).toFixed(1) : "0";
    const totalCost = threads.reduce((sum, t) => sum + (t.run_cost_usd ?? 0), 0);
    const avgCost = threads.length > 0 ? totalCost / threads.length : 0;
    return { totalRuns, successRate, avgCost };
  }, [threads, total]);

  /* ── Pagination ────────────────────────────────────────────────────────── */
  const pageCount = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE);

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

      {/* Approval dialog */}
      {showApprovalDialog && approvals.length > 0 && (
        <ApprovalDialog
          approvals={approvals}
          onClose={() => setShowApprovalDialog(false)}
          onResolved={() => { fetchApprovals(); fetchThreads(); }}
        />
      )}

      {/* Main content */}
      <main style={{ marginLeft: "var(--sidebar-w)" }}>
        {/* Approval alert */}
        {approvals.length > 0 && (
          <button
            onClick={() => setShowApprovalDialog(true)}
            className="flex items-center gap-3 w-full cursor-pointer transition-colors"
            style={{
              padding: "10px 32px",
              background: "var(--amber-dim)",
              borderBottom: "1px solid rgba(251,191,36,0.15)",
              border: "none",
              color: "var(--amber)",
              fontSize: 13,
              fontWeight: 500,
              textAlign: "left",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(251,191,36,0.14)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "var(--amber-dim)"; }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 9v4M12 17h.01" />
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            </svg>
            <span>
              {approvals.length === 1
                ? "1 agent waiting for approval"
                : `${approvals.length} agents waiting for approval`}
            </span>
            <span
              className="inline-flex items-center justify-center"
              style={{
                minWidth: 20,
                height: 20,
                padding: "0 6px",
                borderRadius: 10,
                background: "var(--amber)",
                color: "#000",
                fontSize: 11,
                fontWeight: 700,
                fontFamily: "var(--font-mono)",
              }}
            >
              {approvals.length}
            </span>
            <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-muted)" }}>
              Click to review
            </span>
          </button>
        )}

        {/* Page header */}
        <header
          className="animate-fade-in"
          style={{
            padding: "28px 32px 20px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div className="flex items-start justify-between">
            <div>
              <h1
                style={{
                  margin: 0,
                  fontSize: 22,
                  fontWeight: 700,
                  color: "var(--text)",
                  letterSpacing: "-0.03em",
                  lineHeight: 1.2,
                }}
              >
                Runs
              </h1>
              <p
                style={{
                  margin: "4px 0 0",
                  fontSize: 13,
                  color: "var(--text-muted)",
                }}
              >
                All agents &middot; Last 24h
              </p>
            </div>

            {/* Stats */}
            <div className="flex items-baseline gap-8">
              <div className="text-right">
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: "var(--text)",
                    letterSpacing: "-0.02em",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {stats.totalRuns.toLocaleString()}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 1 }}>
                  Total runs
                </div>
              </div>
              <div className="text-right">
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: "var(--purple)",
                    letterSpacing: "-0.02em",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {stats.successRate}%
                </div>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 1 }}>
                  Success
                </div>
              </div>
              <div className="text-right">
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 700,
                    color: "var(--text)",
                    letterSpacing: "-0.02em",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {formatCost(stats.avgCost)}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 1 }}>
                  Avg cost
                </div>
              </div>
            </div>
          </div>

          {/* Filters row */}
          <div
            className="flex items-center justify-between"
            style={{ marginTop: 16 }}
          >
            {/* Status tabs */}
            <div className="flex items-center gap-1">
              {STATUS_TABS.map((tab) => {
                const active = statusFilter === tab.key;
                const count = statusCounts[tab.key] ?? 0;
                return (
                  <button
                    key={tab.key}
                    onClick={() => { setStatusFilter(tab.key); setOffset(0); }}
                    className="inline-flex items-center gap-1.5 cursor-pointer transition-all"
                    style={{
                      padding: "5px 12px",
                      borderRadius: 6,
                      border: "none",
                      background: active ? "var(--bg-raised)" : "transparent",
                      color: active ? "var(--text)" : "var(--text-muted)",
                      fontSize: 12.5,
                      fontWeight: active ? 500 : 400,
                    }}
                  >
                    {tab.label}
                    {count > 0 && (
                      <span
                        style={{
                          fontSize: 10,
                          fontFamily: "var(--font-mono)",
                          color: active ? "var(--text-secondary)" : "var(--text-dim)",
                          marginLeft: 2,
                        }}
                      >
                        {count}
                      </span>
                    )}
                  </button>
                );
              })}
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
                placeholder="Search runs..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="outline-none"
                style={{
                  width: 220,
                  height: 32,
                  padding: "0 10px 0 30px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "var(--bg-surface)",
                  color: "var(--text)",
                  fontSize: 12.5,
                  fontFamily: "inherit",
                }}
              />
            </div>
          </div>
        </header>

        {/* Table */}
        <div>
          {loading && (
            <div style={{ padding: "16px 32px" }} className="flex flex-col gap-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 42, borderRadius: 6 }} />
              ))}
            </div>
          )}

          {!loading && error && (
            <div style={{ padding: 32 }}>
              <div
                style={{
                  padding: "24px",
                  borderRadius: 8,
                  background: "var(--red-dim)",
                  border: "1px solid rgba(239,68,68,0.15)",
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

          {!loading && !error && filtered.length === 0 && (
            <div
              className="flex flex-col items-center justify-center"
              style={{ padding: "80px 32px", color: "var(--text-muted)" }}
            >
              <div style={{ fontSize: 28, opacity: 0.2, marginBottom: 12 }}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                  <polyline points="13 2 13 9 20 9" />
                </svg>
              </div>
              <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>
                {search || statusFilter !== "all" ? "No runs match" : "No runs yet"}
              </div>
              <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 320 }}>
                {search || statusFilter !== "all"
                  ? "Try clearing your search or filter."
                  : (
                    <>
                      Run your first agent with{" "}
                      <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                        ninetrix run --file agentfile.yaml
                      </code>
                    </>
                  )}
              </div>
            </div>
          )}

          {!loading && !error && filtered.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["RUN ID", "AGENT", "STATUS", "DURATION", "TOKENS", "COST", "MODEL", "STARTED", ""].map((h) => (
                    <th
                      key={h || "__action"}
                      style={{
                        padding: "0 16px",
                        height: 36,
                        fontSize: 10.5,
                        fontWeight: 500,
                        color: "var(--text-dim)",
                        textAlign: h === "DURATION" || h === "TOKENS" || h === "STARTED" ? "right" : h === "COST" ? "right" : "left",
                        letterSpacing: "0.06em",
                        textTransform: "uppercase",
                        borderBottom: "1px solid var(--border)",
                        background: "var(--bg)",
                        whiteSpace: "nowrap",
                        width: h === "" ? 48 : undefined,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => {
                  const isSelected = selectedId === t.thread_id;
                  return (
                    <React.Fragment key={t.thread_id}>
                      <tr
                        onClick={() => setSelectedId(isSelected ? null : t.thread_id)}
                        className="cursor-pointer transition-colors"
                        style={{
                          borderBottom: isSelected ? "none" : "1px solid var(--border)",
                          background: isSelected ? "var(--bg-hover)" : "transparent",
                        }}
                        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
                      >
                        {/* Run ID */}
                        <td style={{ padding: "10px 16px" }}>
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 12.5,
                              color: "var(--text-secondary)",
                              letterSpacing: "-0.01em",
                            }}
                          >
                            {t.thread_id.length > 16 ? t.thread_id.slice(0, 16) : t.thread_id}
                          </span>
                        </td>

                        {/* Agent */}
                        <td style={{ padding: "10px 16px" }}>
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 12.5,
                              color: "var(--text)",
                              fontWeight: 450,
                            }}
                          >
                            {t.agent_name || t.agent_id}
                          </span>
                        </td>

                        {/* Status */}
                        <td style={{ padding: "10px 16px" }}>
                          <StatusBadge status={t.status} />
                        </td>

                        {/* Duration */}
                        <td style={{ padding: "10px 16px", textAlign: "right" }}>
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 12.5,
                              color: "var(--text-secondary)",
                            }}
                          >
                            {formatDuration(t.duration_ms)}
                          </span>
                        </td>

                        {/* Tokens */}
                        <td style={{ padding: "10px 16px", textAlign: "right" }}>
                          <span
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 12.5,
                              color: "var(--text-secondary)",
                            }}
                          >
                            {t.tokens_used > 0 ? formatTokens(t.tokens_used) : "--"}
                          </span>
                        </td>

                        {/* Cost / Budget */}
                        <td style={{ padding: "10px 16px", textAlign: "right" }}>
                          <BudgetInline
                            spent={t.run_cost_usd ?? 0}
                            budget={t.budget_usd ?? 0}
                            softWarned={t.budget_soft_warned ?? false}
                          />
                        </td>

                        {/* Model */}
                        <td style={{ padding: "10px 16px" }}>
                          <span
                            style={{
                              display: "inline-flex",
                              padding: "1px 7px",
                              borderRadius: 4,
                              background: "var(--bg-raised)",
                              border: "1px solid var(--border)",
                              color: "var(--text-secondary)",
                              fontSize: 11,
                              fontWeight: 500,
                              whiteSpace: "nowrap",
                            }}
                          >
                            {shortModel(t.model)}
                          </span>
                        </td>

                        {/* Started */}
                        <td style={{ padding: "10px 16px", textAlign: "right" }}>
                          <span
                            title={new Date(t.started_at).toLocaleString()}
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: 11.5,
                              color: "var(--text-dim)",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {formatRelTime(t.started_at)}
                          </span>
                        </td>

                        {/* Open detail */}
                        <td style={{ padding: "10px 8px", textAlign: "center" }}>
                          <a
                            href="#"
                            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setSelectedId(selectedId === t.thread_id ? null : t.thread_id); }}
                            title="Open trace view"
                            className="inline-flex items-center justify-center transition-colors no-underline"
                            style={{
                              width: 28,
                              height: 28,
                              borderRadius: 6,
                              border: "1px solid var(--border)",
                              background: "transparent",
                              color: "var(--text-muted)",
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.background = "var(--bg-raised)";
                              e.currentTarget.style.color = "var(--text)";
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.background = "transparent";
                              e.currentTarget.style.color = "var(--text-muted)";
                            }}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                              <polyline points="15 3 21 3 21 9" />
                              <line x1="10" y1="14" x2="21" y2="3" />
                            </svg>
                          </a>
                        </td>
                      </tr>

                      {/* Inline trace panel */}
                      {isSelected && (
                        <tr>
                          <td colSpan={9} style={{ padding: 0 }}>
                            {traceLoading ? (
                              <div style={{ padding: "16px 32px" }}>
                                <div className="skeleton" style={{ height: 120, borderRadius: 6 }} />
                              </div>
                            ) : (
                              <TracePanel
                                nodes={traceNodes}
                                threadId={t.thread_id}
                                agentId={t.agent_name || t.agent_id}
                              />
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}

          {/* Pagination */}
          {!loading && !error && pageCount > 1 && (
            <div
              className="flex items-center justify-between"
              style={{
                padding: "10px 32px",
                borderTop: "1px solid var(--border)",
              }}
            >
              <span
                style={{
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-dim)",
                }}
              >
                {offset + 1}&ndash;{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                {[
                  { label: "\u00AB", page: 0, disabled: currentPage === 0 },
                  { label: "\u2039", page: currentPage - 1, disabled: currentPage === 0 },
                  { label: "\u203A", page: currentPage + 1, disabled: currentPage >= pageCount - 1 },
                  { label: "\u00BB", page: pageCount - 1, disabled: currentPage >= pageCount - 1 },
                ].map((btn, i) => (
                  <button
                    key={i}
                    disabled={btn.disabled}
                    onClick={() => { setOffset(btn.page * PAGE_SIZE); setLoading(true); }}
                    className="flex items-center justify-center cursor-pointer transition-colors"
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 5,
                      border: "1px solid var(--border)",
                      background: "transparent",
                      color: btn.disabled ? "var(--text-dim)" : "var(--text-secondary)",
                      fontSize: 14,
                      opacity: btn.disabled ? 0.3 : 1,
                    }}
                  >
                    {btn.label}
                  </button>
                ))}
                <span
                  style={{
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-dim)",
                    padding: "0 8px",
                  }}
                >
                  {currentPage + 1}/{pageCount}
                </span>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
