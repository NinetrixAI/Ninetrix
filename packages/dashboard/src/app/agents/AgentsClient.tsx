"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { listAgents, checkApiStatus, type AgentStats, type ApiStatus } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function formatRelTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return "just now";
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  const d = new Date(iso);
  const now = new Date();
  if (d.getFullYear() === now.getFullYear())
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function normalizeStatus(s: string): "running" | "completed" | "error" | "pending" | "idle" {
  if (s === "in_progress" || s === "started" || s === "running") return "running";
  if (s === "completed" || s === "approved") return "completed";
  if (s === "error" || s === "failed") return "error";
  if (s === "waiting_for_approval" || s === "pending") return "pending";
  return "idle";
}

function shortModel(model: string): string {
  if (!model) return "";
  const m = model.toLowerCase();
  if (m.includes("claude-opus")) return "Opus";
  if (m.includes("claude-sonnet")) return "Sonnet";
  if (m.includes("claude-haiku")) return "Haiku";
  if (m.includes("gpt-4o")) return "GPT-4o";
  if (m.includes("gpt-4")) return "GPT-4";
  if (m.includes("gpt-3.5")) return "GPT-3.5";
  if (m.includes("gemini")) return "Gemini";
  if (m.includes("llama")) return "Llama";
  return model.split("-").slice(-1)[0] || model;
}

function agentColor(id: string): { bg: string; fg: string } {
  const PALETTES = [
    { bg: "rgba(59,130,246,0.15)", fg: "#60A5FA" },
    { bg: "rgba(139,92,246,0.15)", fg: "#A78BFA" },
    { bg: "rgba(16,185,129,0.15)", fg: "#34D399" },
    { bg: "rgba(245,158,11,0.15)", fg: "#FCD34D" },
    { bg: "rgba(239,68,68,0.15)", fg: "#F87171" },
    { bg: "rgba(6,182,212,0.15)", fg: "#22D3EE" },
    { bg: "rgba(236,72,153,0.15)", fg: "#F472B6" },
    { bg: "rgba(132,204,22,0.15)", fg: "#A3E635" },
  ];
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return PALETTES[Math.abs(hash) % PALETTES.length];
}

function agentInitials(id: string): string {
  const parts = id.replace(/[-_]/g, " ").trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return id.slice(0, 2).toUpperCase();
}

// ─── Status Badge ─────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  running:   { bg: "rgba(16,185,129,0.1)",   text: "#10B981", dot: "#10B981", label: "Running"   },
  completed: { bg: "rgba(100,116,139,0.1)",  text: "#94A3B8", dot: "#64748B", label: "Completed" },
  error:     { bg: "rgba(239,68,68,0.1)",    text: "#EF4444", dot: "#EF4444", label: "Failed"    },
  pending:   { bg: "rgba(245,158,11,0.1)",   text: "#F59E0B", dot: "#F59E0B", label: "Pending"   },
  idle:      { bg: "rgba(100,116,139,0.08)", text: "#64748B", dot: "#475569", label: "Idle"      },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[normalizeStatus(status)] ?? STATUS_STYLES.idle;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "2px 8px",
        borderRadius: 4,
        background: s.bg,
        color: s.text,
        fontSize: 11,
        fontWeight: 500,
        letterSpacing: "0.02em",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: s.dot,
          flexShrink: 0,
          animation: normalizeStatus(status) === "running" ? "pulse-ring 1.6s ease-in-out infinite" : "none",
        }}
      />
      {s.label}
    </span>
  );
}

// ─── Success Rate ─────────────────────────────────────────────────────────────

function SuccessRate({ completed, total, errors }: { completed: number; total: number; errors: number }) {
  if (total === 0) return <span style={{ color: "var(--text-muted)", fontSize: 12 }}>—</span>;
  const rate = Math.round((completed / total) * 100);
  const color = rate >= 90 ? "#10B981" : rate >= 70 ? "#F59E0B" : "#EF4444";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 72 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color, fontFamily: "var(--font-jb-mono, monospace)" }}>
          {rate}%
        </span>
        {errors > 0 && (
          <span style={{ fontSize: 10, color: "#EF4444", opacity: 0.7 }}>
            {errors} err
          </span>
        )}
      </div>
      <div style={{ height: 3, background: "var(--border-strong)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${rate}%`, background: color, borderRadius: 2, transition: "width 0.4s ease" }} />
      </div>
    </div>
  );
}

// ─── Top Navigation — matches ThreadsClient exactly ───────────────────────────

function TopNav({
  apiStatus,
  autoRefresh,
  onToggleAutoRefresh,
  onRefresh,
}: {
  apiStatus: ApiStatus | null;
  autoRefresh: boolean;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
}) {
  return (
    <nav
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        height: 48,
        zIndex: 100,
        background: "var(--bg-nav)",
        borderBottom: "1px solid var(--border)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        display: "flex",
        alignItems: "center",
        paddingInline: 20,
        gap: 0,
        transition: "background 0.22s ease, border-color 0.22s ease",
      }}
    >
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginRight: 24 }}>
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: 6,
            overflow: "hidden",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <img
            src="/dashboard/ninetrix-logo.png"
            alt="Ninetrix"
            width={26}
            height={26}
            style={{ borderRadius: 5, display: "block" }}
          />
        </div>
        <span
          style={{
            fontFamily: "var(--font-syne, sans-serif)",
            fontWeight: 700,
            fontSize: 14.5,
            color: "var(--text-primary)",
            letterSpacing: "-0.015em",
            transition: "color 0.22s",
          }}
        >
          Ninetrix
        </span>
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 700,
            color: "var(--accent-blue)",
            background: "var(--accent-blue-dim)",
            border: "1px solid rgba(59,130,246,0.2)",
            borderRadius: 3,
            padding: "1px 6px",
            letterSpacing: "0.07em",
          }}
        >
          LOCAL
        </span>
      </div>

      {/* Nav tabs */}
      <div style={{ display: "flex", gap: 2, flex: 1 }}>
        {[
          { label: "Threads", active: false, href: "/dashboard/threads" },
          { label: "Agents",  active: true,  href: "/dashboard/agents"  },
          { label: "Settings", active: false, href: "#"                  },
        ].map((tab) => (
          <a
            key={tab.label}
            href={tab.href}
            style={{
              display: "inline-flex",
              alignItems: "center",
              height: 30,
              padding: "0 12px",
              borderRadius: 6,
              fontSize: 13,
              fontWeight: tab.active ? 500 : 400,
              color: tab.active ? "var(--text-primary)" : "var(--text-muted)",
              background: tab.active ? "var(--border-strong)" : "transparent",
              textDecoration: "none",
              cursor: tab.active ? "default" : "pointer",
              transition: "background 0.15s, color 0.15s",
            }}
          >
            {tab.label}
          </a>
        ))}
      </div>

      {/* Right controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {/* Auto-refresh */}
        <button
          onClick={onToggleAutoRefresh}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 10px",
            borderRadius: 5,
            border: "1px solid var(--border-strong)",
            background: autoRefresh ? "var(--accent-blue-dim)" : "transparent",
            color: autoRefresh ? "var(--accent-blue)" : "var(--text-muted)",
            fontSize: 12,
            cursor: "pointer",
            fontWeight: 500,
            transition: "background 0.15s, color 0.15s",
          }}
        >
          <span
            style={{
              display: "inline-block",
              animation: autoRefresh ? "spin 2s linear infinite" : "none",
              fontSize: 12,
              lineHeight: 1,
            }}
          >
            ↻
          </span>
          Live
        </button>

        {/* Manual refresh */}
        <button
          onClick={onRefresh}
          title="Refresh now"
          style={{
            width: 28,
            height: 28,
            borderRadius: 5,
            border: "1px solid var(--border-strong)",
            background: "transparent",
            color: "var(--text-secondary)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 15,
            transition: "background 0.12s, color 0.12s",
          }}
        >
          ↻
        </button>

        {/* Divider */}
        <div style={{ width: 1, height: 18, background: "var(--border-strong)" }} />

        {/* API status */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 10px",
            borderRadius: 5,
            background: apiStatus == null
              ? "transparent"
              : apiStatus.connected
              ? "rgba(16,185,129,0.07)"
              : "rgba(239,68,68,0.07)",
            border: "1px solid",
            borderColor: apiStatus == null
              ? "var(--border)"
              : apiStatus.connected
              ? "rgba(16,185,129,0.2)"
              : "rgba(239,68,68,0.2)",
            transition: "background 0.2s, border-color 0.2s",
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: apiStatus == null
                ? "var(--text-muted)"
                : apiStatus.connected
                ? "#10B981"
                : "#EF4444",
              flexShrink: 0,
              animation: apiStatus?.connected ? "pulse-ring 2s ease-in-out infinite" : "none",
            }}
          />
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: apiStatus == null
                ? "var(--text-muted)"
                : apiStatus.connected
                ? "#10B981"
                : "#EF4444",
            }}
          >
            {apiStatus == null
              ? "checking…"
              : apiStatus.connected
              ? `API · ${apiStatus.latencyMs}ms`
              : "run: ninetrix dev"}
          </span>
        </div>

        {/* Divider */}
        <div style={{ width: 1, height: 18, background: "var(--border-strong)" }} />

        <ThemeToggle />
      </div>
    </nav>
  );
}

// ─── Sort helpers ─────────────────────────────────────────────────────────────

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

const STATUS_FILTERS = [
  { key: "all",     label: "All"     },
  { key: "running", label: "Running" },
  { key: "error",   label: "Error"   },
];

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function AgentsClient() {
  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("last_seen");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      const data = await listAgents();
      setAgents(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    const s = await checkApiStatus();
    setApiStatus(s);
  }, []);

  useEffect(() => {
    fetchAgents();
    fetchStatus();
  }, [fetchAgents, fetchStatus]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh) {
      intervalRef.current = setInterval(() => {
        fetchAgents();
        fetchStatus();
      }, 5000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, fetchAgents, fetchStatus]);

  const counts = {
    all:     agents.length,
    running: agents.filter((a) => normalizeStatus(a.last_status) === "running").length,
    error:   agents.filter((a) => normalizeStatus(a.last_status) === "error").length,
  };

  const filtered = agents.filter((a) => {
    const q = search.toLowerCase();
    const matchSearch = !q || a.agent_id.toLowerCase().includes(q) ||
      a.models.some((m) => m.toLowerCase().includes(q));
    const matchStatus =
      statusFilter === "all" ||
      (statusFilter === "running" && normalizeStatus(a.last_status) === "running") ||
      (statusFilter === "error"   && normalizeStatus(a.last_status) === "error");
    return matchSearch && matchStatus;
  });

  const sorted = sortAgents(filtered, sortKey, sortDir);

  function handleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  }

  function SortArrow({ col }: { col: SortKey }) {
    if (sortKey !== col) return <span style={{ opacity: 0.25, fontSize: 9, marginLeft: 4 }}>⇅</span>;
    return <span style={{ fontSize: 9, color: "var(--accent-blue)", marginLeft: 4 }}>{sortDir === "asc" ? "↑" : "↓"}</span>;
  }

  const thStyle = (col: SortKey, align: "left" | "right" = "left"): React.CSSProperties => ({
    padding: "0 16px",
    height: 36,
    fontSize: 11,
    fontWeight: 500,
    color: sortKey === col ? "var(--text-secondary)" : "var(--text-muted)",
    textAlign: align,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    cursor: "pointer",
    userSelect: "none",
    whiteSpace: "nowrap",
    borderBottom: "1px solid var(--border)",
    background: "var(--bg-base)",
  });

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-base)" }}>
      <TopNav
        apiStatus={apiStatus}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        onRefresh={() => { setLoading(true); fetchAgents(); fetchStatus(); }}
      />

      <div style={{ paddingTop: 48 }}>
        {/* Page header */}
        <div style={{ padding: "22px 24px 16px", borderBottom: "1px solid var(--border)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "flex-end",
              justifyContent: "space-between",
              marginBottom: 14,
            }}
          >
            <div>
              <h1
                style={{
                  margin: 0,
                  fontSize: 20,
                  fontWeight: 700,
                  fontFamily: "var(--font-syne, sans-serif)",
                  color: "var(--text-primary)",
                  letterSpacing: "-0.02em",
                  lineHeight: 1.2,
                }}
              >
                Agents
              </h1>
              <p style={{ margin: "4px 0 0", fontSize: 12.5, color: "var(--text-muted)" }}>
                {agents.length} total agents · local mode
              </p>
            </div>

            {/* Search */}
            <div style={{ position: "relative" }}>
              <span
                style={{
                  position: "absolute",
                  left: 10,
                  top: "50%",
                  transform: "translateY(-50%)",
                  fontSize: 12,
                  color: "var(--text-dim)",
                  pointerEvents: "none",
                }}
              >
                ⌕
              </span>
              <input
                type="text"
                placeholder="Search agents, models…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  width: 230,
                  height: 32,
                  padding: "0 10px 0 28px",
                  borderRadius: 6,
                  border: "1px solid var(--border-strong)",
                  background: "var(--border)",
                  color: "var(--text-primary)",
                  fontSize: 12.5,
                  outline: "none",
                  fontFamily: "inherit",
                }}
              />
            </div>
          </div>

          {/* Filter tabs */}
          <div style={{ display: "flex", gap: 4 }}>
            {STATUS_FILTERS.map((f) => {
              const isActive = statusFilter === f.key;
              const count = counts[f.key as keyof typeof counts] ?? 0;
              return (
                <button
                  key={f.key}
                  onClick={() => setStatusFilter(f.key)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 12px",
                    borderRadius: 5,
                    border: isActive
                      ? "1px solid rgba(59,130,246,0.3)"
                      : "1px solid var(--border-strong)",
                    background: isActive ? "var(--accent-blue-dim)" : "transparent",
                    color: isActive ? "var(--accent-blue)" : "var(--text-muted)",
                    fontSize: 12,
                    fontWeight: isActive ? 600 : 400,
                    cursor: "pointer",
                    transition: "all 0.12s",
                  }}
                >
                  {f.label}
                  {count > 0 && (
                    <span
                      style={{
                        fontSize: 10,
                        background: isActive ? "rgba(59,130,246,0.15)" : "var(--border-strong)",
                        color: isActive ? "var(--accent-blue)" : "var(--text-muted)",
                        padding: "0 5px",
                        borderRadius: 10,
                        minWidth: 16,
                        textAlign: "center",
                      }}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Table area */}
        <div>
          {loading && (
            <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 6 }}>
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 44, borderRadius: 6 }} />
              ))}
            </div>
          )}

          {!loading && error && (
            <div
              style={{
                margin: "24px",
                padding: "20px 24px",
                borderRadius: 8,
                background: "rgba(239,68,68,0.07)",
                border: "1px solid rgba(239,68,68,0.15)",
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: "#EF4444", marginBottom: 6 }}>
                Cannot reach local API
              </div>
              <div
                style={{
                  fontSize: 12,
                  fontFamily: "var(--font-jb-mono, monospace)",
                  color: "var(--text-secondary)",
                  marginBottom: 12,
                }}
              >
                {error}
              </div>
              <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
                Run{" "}
                <code
                  style={{
                    background: "var(--border)",
                    padding: "1px 6px",
                    borderRadius: 4,
                    fontFamily: "var(--font-jb-mono, monospace)",
                  }}
                >
                  ninetrix dev
                </code>
                {" "}to start the local stack
              </div>
            </div>
          )}

          {!loading && !error && sorted.length === 0 && (
            <div
              style={{
                padding: "64px 24px",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 12,
                color: "var(--text-muted)",
              }}
            >
              <div style={{ fontSize: 28, opacity: 0.3 }}>◈</div>
              <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--text-secondary)" }}>
                {search || statusFilter !== "all" ? "No agents match" : "No agents yet"}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--text-muted)", textAlign: "center", maxWidth: 320 }}>
                {search || statusFilter !== "all"
                  ? "Try clearing your search or filter."
                  : "Run your first agent with agentfile run --file agentfile.yaml"}
              </div>
            </div>
          )}

          {!loading && !error && sorted.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th onClick={() => handleSort("agent_id")} style={thStyle("agent_id")}>
                    Agent <SortArrow col="agent_id" />
                  </th>
                  <th style={{ ...thStyle("agent_id"), cursor: "default" }}>Status</th>
                  <th onClick={() => handleSort("total_runs")} style={thStyle("total_runs", "right")}>
                    Runs <SortArrow col="total_runs" />
                  </th>
                  <th onClick={() => handleSort("success_rate")} style={thStyle("success_rate")}>
                    Success Rate <SortArrow col="success_rate" />
                  </th>
                  <th onClick={() => handleSort("total_tokens")} style={thStyle("total_tokens", "right")}>
                    Tokens <SortArrow col="total_tokens" />
                  </th>
                  <th style={{ ...thStyle("agent_id"), cursor: "default" }}>Models</th>
                  <th onClick={() => handleSort("last_seen")} style={thStyle("last_seen", "right")}>
                    Last Active <SortArrow col="last_seen" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((agent) => {
                  const colors = agentColor(agent.agent_id);
                  return (
                    <tr
                      key={agent.agent_id}
                      style={{ borderBottom: "1px solid var(--border)", transition: "background 0.1s" }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(148,163,184,0.04)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      {/* Agent */}
                      <td style={{ padding: "10px 16px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <div
                            style={{
                              width: 28,
                              height: 28,
                              borderRadius: 7,
                              background: colors.bg,
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              fontSize: 10,
                              fontWeight: 700,
                              color: colors.fg,
                              fontFamily: "var(--font-jb-mono, monospace)",
                              flexShrink: 0,
                              letterSpacing: "-0.02em",
                            }}
                          >
                            {agentInitials(agent.agent_id)}
                          </div>
                          <span
                            style={{
                              fontSize: 13,
                              fontWeight: 500,
                              color: "var(--text-primary)",
                              fontFamily: "var(--font-jb-mono, monospace)",
                              letterSpacing: "-0.02em",
                            }}
                          >
                            {agent.agent_id}
                          </span>
                        </div>
                      </td>

                      {/* Status */}
                      <td style={{ padding: "10px 16px" }}>
                        <StatusBadge status={agent.last_status} />
                      </td>

                      {/* Runs */}
                      <td style={{ padding: "10px 16px", textAlign: "right" }}>
                        <span
                          style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: "var(--text-primary)",
                            fontFamily: "var(--font-jb-mono, monospace)",
                          }}
                        >
                          {agent.total_runs}
                        </span>
                      </td>

                      {/* Success rate */}
                      <td style={{ padding: "10px 16px" }}>
                        <SuccessRate
                          completed={agent.completed_runs}
                          total={agent.total_runs}
                          errors={agent.error_runs}
                        />
                      </td>

                      {/* Tokens */}
                      <td style={{ padding: "10px 16px", textAlign: "right" }}>
                        <span
                          style={{
                            fontSize: 12.5,
                            color: "var(--text-secondary)",
                            fontFamily: "var(--font-jb-mono, monospace)",
                          }}
                        >
                          {formatTokens(agent.total_tokens)}
                        </span>
                      </td>

                      {/* Models */}
                      <td style={{ padding: "10px 16px" }}>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                          {agent.models.slice(0, 3).map((m) => (
                            <span
                              key={m}
                              title={m}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                padding: "1px 7px",
                                borderRadius: 3,
                                background: "var(--border)",
                                border: "1px solid var(--border-strong)",
                                color: "var(--text-secondary)",
                                fontSize: 11,
                                fontWeight: 500,
                                letterSpacing: "0.02em",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {shortModel(m)}
                            </span>
                          ))}
                          {agent.models.length > 3 && (
                            <span
                              style={{
                                padding: "1px 6px",
                                borderRadius: 3,
                                background: "var(--border)",
                                color: "var(--text-muted)",
                                fontSize: 10,
                              }}
                            >
                              +{agent.models.length - 3}
                            </span>
                          )}
                          {agent.models.length === 0 && (
                            <span style={{ color: "var(--text-muted)", fontSize: 12 }}>—</span>
                          )}
                        </div>
                      </td>

                      {/* Last active */}
                      <td style={{ padding: "10px 16px", textAlign: "right" }}>
                        <span
                          title={new Date(agent.last_seen).toLocaleString()}
                          style={{
                            fontSize: 12,
                            color: "var(--text-muted)",
                            fontFamily: "var(--font-jb-mono, monospace)",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {formatRelTime(agent.last_seen)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
