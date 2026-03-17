"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { Highlight, type PrismTheme } from "prism-react-renderer";
import {
  listThreads,
  getThreadTimeline,
  checkApiStatus,
  type ThreadSummary,
  type TimelineEvent,
  type ApiStatus,
} from "@/lib/api";
import { timelineEventsToTraceNodes, calcTotalMs, type TraceNode } from "@/lib/trace";
import ThemeToggle from "@/components/ThemeToggle";
import StatusBadge, { normalizeStatus } from "@/components/StatusBadge";

// ─── JSON theme ──────────────────────────────────────────────────────────────

const JSON_THEME: PrismTheme = {
  plain: { backgroundColor: "transparent", color: "#8A95A8" },
  styles: [
    { types: ["property"], style: { color: "#60A5FA" } },
    { types: ["string"], style: { color: "#4ADE80" } },
    { types: ["number", "boolean", "null", "keyword"], style: { color: "#4ADE80" } },
    { types: ["punctuation", "operator"], style: { color: "#4B5563" } },
  ],
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function formatRelTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return "just now";
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatAbsTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3,
  } as Intl.DateTimeFormatOptions);
}

function shortModel(model: string): string {
  const map: Record<string, string> = {
    "claude-opus": "Opus", "claude-sonnet": "Sonnet", "claude-haiku": "Haiku",
    "gpt-4o": "GPT-4o", "gpt-4": "GPT-4", "gpt-3.5": "GPT-3.5",
  };
  for (const [key, val] of Object.entries(map)) {
    if (model.toLowerCase().includes(key)) return val;
  }
  return model.split("-").slice(-1)[0] || model;
}

function getTickInterval(totalMs: number): number {
  if (totalMs < 200) return 50;
  if (totalMs < 1000) return 200;
  if (totalMs < 5000) return 1000;
  if (totalMs < 20000) return 2000;
  if (totalMs < 60000) return 10000;
  if (totalMs < 300000) return 30000;
  return 60000;
}

function formatTickLabel(ms: number): string {
  if (ms === 0) return "0";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1)}s`;
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const NODE_CONFIG = {
  llm: { color: "#3B82F6", dimColor: "rgba(59,130,246,0.12)", icon: "◈", label: "LLM" },
  tool: { color: "#22C55E", dimColor: "rgba(34,197,94,0.12)", icon: "◎", label: "Tool" },
  thinking: { color: "#F59E0B", dimColor: "rgba(245,158,11,0.12)", icon: "◉", label: "Think" },
  handoff: { color: "#8B5CF6", dimColor: "rgba(139,92,246,0.12)", icon: "◀▶", label: "Handoff" },
};

const TRIGGER_ICONS: Record<string, string> = {
  webhook: "⚡", schedule: "⏱", api: "⬡", manual: "▶", github: "◆",
};

const zoomBtnStyle: React.CSSProperties = {
  display: "inline-flex", alignItems: "center", justifyContent: "center",
  width: 26, height: 22, borderRadius: 4, border: "1px solid var(--border-strong)",
  background: "var(--bg-elevated)", color: "var(--text-secondary)", cursor: "pointer",
  fontSize: 13, fontWeight: 600, lineHeight: 1, padding: 0,
  fontFamily: "var(--font-jb-mono, monospace)",
};

// ─── TriggerChip ─────────────────────────────────────────────────────────────

function TriggerChip({ trigger }: { trigger: string }) {
  const t = trigger?.toLowerCase() || "api";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4, padding: "1px 7px",
      borderRadius: 3, background: "var(--border)", color: "var(--text-secondary)",
      fontSize: 11, fontWeight: 500, letterSpacing: "0.03em",
      border: "1px solid var(--border-strong)",
    }}>
      {TRIGGER_ICONS[t] ?? "○"} {t.charAt(0).toUpperCase() + t.slice(1)}
    </span>
  );
}

// ─── ContentBlock ─────────────────────────────────────────────────────────────

function ContentBlock({ label, content, color }: { label: string; content: string; color: string }) {
  const [expanded, setExpanded] = useState(true);
  let rendered: React.ReactNode;
  // Try to detect JSON
  const trimmed = content.trim();
  const isJson = (trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 0;

  if (isJson && expanded) {
    try {
      const pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
      rendered = (
        <Highlight theme={JSON_THEME} code={pretty} language="json">
          {({ tokens, getLineProps, getTokenProps }) => (
            <pre style={{
              margin: 0, padding: "0 10px 10px", fontSize: 11,
              fontFamily: "var(--font-jb-mono, monospace)",
              lineHeight: 1.7, overflowX: "auto", whiteSpace: "pre-wrap",
              wordBreak: "break-word", maxHeight: 320, overflowY: "auto",
            }}>
              {tokens.map((line, i) => (
                <div key={i} {...getLineProps({ line })}>
                  {line.map((token, key) => (
                    <span key={key} {...getTokenProps({ token })} />
                  ))}
                </div>
              ))}
            </pre>
          )}
        </Highlight>
      );
    } catch {
      rendered = (
        <pre style={{
          margin: 0, padding: "0 10px 10px", fontSize: 11,
          fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-primary)",
          lineHeight: 1.7, overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>{content}</pre>
      );
    }
  } else if (expanded) {
    rendered = (
      <pre style={{
        margin: 0, padding: "0 10px 10px", fontSize: 11,
        fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-primary)",
        lineHeight: 1.7, overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
        maxHeight: 320, overflowY: "auto",
      }}>{content || "(empty)"}</pre>
    );
  }

  return (
    <div style={{
      marginTop: 8, borderRadius: 6, border: `1px solid ${color}22`,
      background: `${color}08`, overflow: "hidden",
    }}>
      <button onClick={() => setExpanded((e) => !e)} style={{
        width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "6px 10px", background: "transparent", border: "none", cursor: "pointer",
        color: "#8A95A8", fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textAlign: "left",
      }}>
        <span style={{ color }}>{label}</span>
        <span style={{ fontSize: 10, opacity: 0.5 }}>{expanded ? "▲" : "▼"}</span>
      </button>
      {rendered}
    </div>
  );
}

// ─── Navbar ───────────────────────────────────────────────────────────────────

function ObservabilityNav({
  thread,
  apiStatus,
  isLive,
}: {
  thread: ThreadSummary | null;
  apiStatus: ApiStatus | null;
  isLive: boolean;
}) {
  return (
    <nav style={{
      position: "fixed", top: 0, left: 0, right: 0, height: 48, zIndex: 100,
      background: "var(--bg-nav)", borderBottom: "1px solid var(--border)",
      backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)",
      display: "flex", alignItems: "center", paddingInline: 20, gap: 0,
      transition: "background 0.22s ease, border-color 0.22s ease",
    }}>
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginRight: 24 }}>
        <div style={{ width: 26, height: 26, borderRadius: 6, overflow: "hidden", flexShrink: 0 }}>
          <img src="/dashboard/ninetrix-logo.png" alt="Ninetrix" width={26} height={26} style={{ borderRadius: 5, display: "block" }} />
        </div>
        <span style={{ fontFamily: "var(--font-syne, sans-serif)", fontWeight: 700, fontSize: 14.5, color: "var(--text-primary)", letterSpacing: "-0.015em" }}>
          Ninetrix
        </span>
        <span style={{ fontSize: 9.5, fontWeight: 700, color: "var(--accent-blue)", background: "var(--accent-blue-dim)", border: "1px solid rgba(59,130,246,0.2)", borderRadius: 3, padding: "1px 6px", letterSpacing: "0.07em" }}>
          LOCAL
        </span>
      </div>

      {/* Breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1 }}>
        {[
          { label: "Threads", href: "/dashboard/threads" },
          { label: "Agents", href: "/dashboard/agents" },
        ].map((tab) => (
          <a key={tab.label} href={tab.href} style={{
            display: "inline-flex", alignItems: "center", height: 30, padding: "0 12px",
            borderRadius: 6, fontSize: 13, fontWeight: 400, color: "var(--text-muted)",
            background: "transparent", textDecoration: "none", transition: "background 0.15s, color 0.15s",
          }}>
            {tab.label}
          </a>
        ))}
        <div style={{ width: 1, height: 14, background: "var(--border-strong)", margin: "0 4px" }} />
        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>›</span>
        <code style={{
          fontSize: 11, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-secondary)",
          background: "var(--border)", padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border-strong)",
        }}>
          {thread?.thread_id ?? "…"}
        </code>
        {thread && <StatusBadge status={thread.status} />}
        {isLive && (
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10.5, fontWeight: 600,
            color: "#10B981", background: "rgba(16,185,129,0.1)", border: "1px solid rgba(16,185,129,0.2)",
            borderRadius: 3, padding: "1px 7px", letterSpacing: "0.04em",
          }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#10B981", animation: "pulse-ring 1.6s ease-in-out infinite" }} />
            LIVE
          </span>
        )}
      </div>

      {/* Right: API status + theme */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <a href="/dashboard/threads" style={{
          display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 10px",
          borderRadius: 5, border: "1px solid var(--border-strong)", background: "transparent",
          color: "var(--text-muted)", fontSize: 12, cursor: "pointer", fontWeight: 500,
          textDecoration: "none", transition: "background 0.15s, color 0.15s",
        }}>
          ← Back
        </a>
        <div style={{ width: 1, height: 18, background: "var(--border-strong)" }} />
        <div style={{
          display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 5,
          background: apiStatus?.connected ? "rgba(16,185,129,0.07)" : "rgba(239,68,68,0.07)",
          border: "1px solid", borderColor: apiStatus?.connected ? "rgba(16,185,129,0.2)" : "rgba(239,68,68,0.2)",
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: apiStatus?.connected ? "#10B981" : "#EF4444", flexShrink: 0,
            animation: apiStatus?.connected ? "pulse-ring 2s ease-in-out infinite" : "none",
          }} />
          <span style={{
            fontSize: 11, fontWeight: 500, fontFamily: "var(--font-jb-mono, monospace)",
            color: apiStatus?.connected ? "#10B981" : "#EF4444",
          }}>
            {apiStatus == null ? "checking…" : apiStatus.connected ? `API · ${apiStatus.latencyMs}ms` : "run: ninetrix dev"}
          </span>
        </div>
        <div style={{ width: 1, height: 18, background: "var(--border-strong)" }} />
        <ThemeToggle />
      </div>
    </nav>
  );
}

// ─── StatLine ─────────────────────────────────────────────────────────────────

function StatLine({ items }: { items: { label: string; value: string; accent?: string }[] }) {
  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", rowGap: 8, columnGap: 0 }}>
      {items.map((item, i) => (
        <span key={item.label} style={{ display: "inline-flex", alignItems: "center" }}>
          {/* Vertical divider before every item except the first */}
          {i > 0 && (
            <span style={{
              display: "inline-block", width: 1, height: 14,
              background: "var(--border-strong)", margin: "0 16px", flexShrink: 0,
            }} />
          )}
          <span style={{ display: "inline-flex", alignItems: "baseline", gap: 6 }}>
            <span style={{
              fontSize: 10, fontWeight: 700, letterSpacing: "0.07em",
              color: "var(--text-dim)", textTransform: "uppercase",
            }}>
              {item.label}
            </span>
            <span style={{
              fontSize: 13, fontFamily: "var(--font-jb-mono, monospace)", fontWeight: 500,
              color: item.accent ?? "var(--text-primary)",
            }}>
              {item.value}
            </span>
          </span>
        </span>
      ))}
    </div>
  );
}

// ─── AgentFlow ────────────────────────────────────────────────────────────────

const AGENT_COLORS = ["#3B82F6", "#8B5CF6", "#22C55E", "#F59E0B", "#06B6D4", "#EF4444"];

function AgentFlow({ agents }: { agents: string[] }) {
  if (!agents || agents.length === 0) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 4 }}>
      {agents.map((agent, i) => (
        <span key={agent} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "4px 10px", borderRadius: 5,
            background: `${AGENT_COLORS[i % AGENT_COLORS.length]}14`,
            border: `1px solid ${AGENT_COLORS[i % AGENT_COLORS.length]}30`,
            color: AGENT_COLORS[i % AGENT_COLORS.length],
            fontSize: 11.5, fontWeight: 500, fontFamily: "var(--font-jb-mono, monospace)",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: AGENT_COLORS[i % AGENT_COLORS.length],
            }} />
            {agent}
          </span>
          {i < agents.length - 1 && (
            <span style={{ fontSize: 11, color: "var(--text-dim)", margin: "0 2px" }}>→</span>
          )}
        </span>
      ))}
    </div>
  );
}

// ─── GanttChart ───────────────────────────────────────────────────────────────

const LABEL_W = 200;
const ROW_H = 28;
const RULER_H = 32;
const DURATION_W = 64;
const DEFAULT_VIEW_MS = 6000;
const MIN_VIEW_MS = 300;

function GanttChart({
  nodes,
  thread,
  liveMs,
  selectedId,
  onSelect,
}: {
  nodes: TraceNode[];
  thread: ThreadSummary;
  liveMs: number;
  selectedId: string | null;
  onSelect: (n: TraceNode) => void;
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: TraceNode } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(800);
  const [viewWindowMs, setViewWindowMs] = useState(DEFAULT_VIEW_MS);
  const initializedRef = useRef(false);
  const isRunning = normalizeStatus(thread.status) === "running";

  const totalMs = Math.max(liveMs, 1);

  // Track scroll container's visible width
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  // Set viewWindowMs once we have real data (only on first meaningful totalMs)
  useEffect(() => {
    if (!initializedRef.current && totalMs > 100) {
      initializedRef.current = true;
      setViewWindowMs(Math.min(totalMs, DEFAULT_VIEW_MS));
    }
  }, [totalMs]);

  // Pixel math: trackWidth is the scrollable bar area width (viewport minus fixed columns)
  const trackWidth = Math.max(containerWidth - LABEL_W - DURATION_W, 100);
  const pxPerMs = trackWidth / viewWindowMs;
  const chartAreaPx = Math.max(trackWidth, Math.round(pxPerMs * totalMs));

  // Ticks covering full totalMs range, interval sized for visible window
  const tickInterval = getTickInterval(viewWindowMs);
  const rawTicks: number[] = [];
  for (let t = 0; t <= totalMs + tickInterval; t += tickInterval) {
    if (t > totalMs * 1.05) break;
    rawTicks.push(Math.min(t, totalMs));
  }
  if (rawTicks[rawTicks.length - 1] < totalMs) rawTicks.push(totalMs);
  const uniqueTicks = Array.from(new Set(rawTicks));

  // Group nodes by agentId for multi-agent display
  const agentMap = useMemo(() => {
    const map = new Map<string, TraceNode[]>();
    for (const n of nodes) {
      const arr = map.get(n.agentId) ?? [];
      arr.push(n);
      map.set(n.agentId, arr);
    }
    return map;
  }, [nodes]);

  const isMultiAgent = agentMap.size > 1;

  // Zoom controls
  const zoomIn = () => setViewWindowMs((v) => Math.max(MIN_VIEW_MS, v / 1.6));
  const zoomOut = () => setViewWindowMs((v) => Math.min(totalMs, v * 1.6));
  const zoomDefault = () => {
    setViewWindowMs(Math.min(totalMs, DEFAULT_VIEW_MS));
    if (scrollRef.current) scrollRef.current.scrollLeft = 0;
  };
  const zoomFit = () => {
    setViewWindowMs(totalMs);
    if (scrollRef.current) scrollRef.current.scrollLeft = 0;
  };

  const zoomLabel = viewWindowMs < 1000
    ? `${Math.round(viewWindowMs)}ms`
    : `${(viewWindowMs / 1000).toFixed(1)}s`;

  // Shared bg for sticky cells (matches surface, can't be transparent in scroll container)
  const stickyBg = "var(--bg-surface)";

  return (
    <div style={{ width: "100%" }}>

      {/* ── Zoom toolbar ── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 5, padding: "6px 10px 8px",
        borderBottom: "1px solid var(--border)",
      }}>
        <span style={{
          fontSize: 9.5, fontWeight: 700, letterSpacing: "0.09em",
          color: "var(--text-dim)", fontFamily: "var(--font-jb-mono, monospace)", marginRight: 2,
        }}>ZOOM</span>
        <button onClick={zoomIn} title="Zoom in (show less time)" style={zoomBtnStyle}>+</button>
        <button onClick={zoomOut} title="Zoom out (show more time)" style={zoomBtnStyle}>−</button>
        <span style={{
          fontSize: 10, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-secondary)",
          padding: "1px 7px", borderRadius: 3, background: "var(--border)",
          border: "1px solid var(--border-strong)", minWidth: 42, textAlign: "center",
        }}>
          {zoomLabel}/view
        </span>
        <button onClick={zoomDefault} title="Reset to 6s view" style={{ ...zoomBtnStyle, width: "auto", padding: "0 8px", fontSize: 11 }}>6s</button>
        <button onClick={zoomFit} title="Fit entire trace" style={{ ...zoomBtnStyle, width: "auto", padding: "0 8px", fontSize: 11 }}>Fit</button>
        {totalMs > viewWindowMs && (
          <span style={{
            fontSize: 10, color: "var(--text-dim)", marginLeft: 4,
            fontFamily: "var(--font-jb-mono, monospace)",
          }}>
            · scroll to see full {(totalMs / 1000).toFixed(1)}s
          </span>
        )}
      </div>

      {/* ── Scrollable chart ── */}
      <div ref={scrollRef} style={{ width: "100%", overflowX: "auto", overflowY: "visible" }}>
        {/* Inner canvas — wider than viewport when zoomed in */}
        <div style={{ width: LABEL_W + chartAreaPx + DURATION_W, minWidth: "100%" }}>

          {/* Time Ruler */}
          <div style={{
            display: "flex", height: RULER_H, alignItems: "flex-end", paddingBottom: 6,
            borderBottom: "1px solid var(--border)", position: "sticky", top: 0,
            background: stickyBg, zIndex: 10,
          }}>
            {/* Label spacer — sticky left */}
            <div style={{
              width: LABEL_W, flexShrink: 0,
              position: "sticky", left: 0, zIndex: 12,
              background: stickyBg,
            }} />
            {/* Tick area */}
            <div style={{ width: chartAreaPx, flexShrink: 0, position: "relative" }}>
              {uniqueTicks.map((t) => {
                const px = Math.round(t * pxPerMs);
                return (
                  <span key={t} style={{
                    position: "absolute", left: px,
                    transform: t === totalMs ? "translateX(-100%)" : t === 0 ? "none" : "translateX(-50%)",
                    fontSize: 10, fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-dim)", whiteSpace: "nowrap", lineHeight: 1,
                  }}>
                    {formatTickLabel(t)}
                  </span>
                );
              })}
              {isRunning && (
                <span style={{
                  position: "absolute", left: chartAreaPx, fontSize: 9.5,
                  fontFamily: "var(--font-jb-mono, monospace)", color: "#10B981",
                  whiteSpace: "nowrap", transform: "translateX(-100%)", fontWeight: 600,
                }}>
                  NOW
                </span>
              )}
            </div>
            {/* Duration spacer — sticky right */}
            <div style={{
              width: DURATION_W, flexShrink: 0,
              position: "sticky", right: 0, zIndex: 12,
              background: stickyBg,
            }} />
          </div>

          {/* Grid + Bars */}
          <div style={{ position: "relative" }}>
            {/* Background grid lines (absolute, offset by LABEL_W) */}
            {uniqueTicks.slice(1).map((t) => (
              <div key={t} style={{
                position: "absolute",
                left: LABEL_W + Math.round(t * pxPerMs),
                top: 0, bottom: 0, width: 1,
                background: "rgba(148,163,184,0.05)",
                zIndex: 0, pointerEvents: "none",
              }} />
            ))}

            {/* Live cursor */}
            {isRunning && (
              <div style={{
                position: "absolute",
                left: LABEL_W + chartAreaPx,
                top: 0, bottom: 0, width: 2,
                background: "rgba(16,185,129,0.6)", zIndex: 5, pointerEvents: "none",
                boxShadow: "0 0 8px rgba(16,185,129,0.4)",
              }} />
            )}

            {/* Rows */}
            {nodes.map((node, idx) => {
              const cfg = NODE_CONFIG[node.type];
              const leftPx = Math.round(node.startOffsetMs * pxPerMs);
              const widthPx = node.durationMs
                ? Math.max(4, Math.round(node.durationMs * pxPerMs))
                : node.status === "running"
                  ? Math.max(8, Math.round((liveMs - node.startOffsetMs) * pxPerMs))
                  : 4;
              const isHovered = hoveredId === node.id;
              const isSelected = selectedId === node.id;
              const isNodeRunning = node.status === "running";
              const isError = node.status === "error";

              const prevNode = nodes[idx - 1];
              const showAgentHeader = isMultiAgent && node.agentId !== prevNode?.agentId;

              // Sticky cell background: must be opaque
              const rowBg = isSelected ? cfg.dimColor : isHovered ? "rgba(30,30,36,1)" : stickyBg;

              return (
                <div key={node.id}>
                  {showAgentHeader && (
                    <div style={{
                      display: "flex", alignItems: "center", height: 22,
                      borderTop: idx > 0 ? "1px solid var(--border)" : "none",
                      marginTop: idx > 0 ? 4 : 0,
                    }}>
                      <div style={{
                        width: LABEL_W, flexShrink: 0,
                        position: "sticky", left: 0, zIndex: 4,
                        background: stickyBg, paddingLeft: 8, height: "100%",
                        display: "flex", alignItems: "center",
                      }}>
                        <span style={{
                          fontSize: 9.5, fontWeight: 700, letterSpacing: "0.07em",
                          color: "var(--text-dim)", fontFamily: "var(--font-jb-mono, monospace)",
                        }}>
                          AGENT: {node.agentId}
                        </span>
                      </div>
                    </div>
                  )}

                  <div
                    onMouseEnter={(e) => { setHoveredId(node.id); setTooltip({ x: e.clientX, y: e.clientY, node }); }}
                    onMouseMove={(e) => { setTooltip((prev) => prev ? { ...prev, x: e.clientX, y: e.clientY } : null); }}
                    onMouseLeave={() => { setHoveredId(null); setTooltip(null); }}
                    onClick={() => onSelect(node)}
                    style={{
                      display: "flex", alignItems: "stretch", height: ROW_H,
                      cursor: "pointer",
                      background: isSelected ? cfg.dimColor : isHovered ? "rgba(148,163,184,0.04)" : "transparent",
                      transition: "background 0.1s",
                    }}
                  >
                    {/* Label — sticky left */}
                    <div style={{
                      width: LABEL_W, flexShrink: 0,
                      display: "flex", alignItems: "center", gap: 6,
                      paddingLeft: 8 + node.depth * 12, paddingRight: 10,
                      position: "sticky", left: 0, zIndex: 3,
                      background: rowBg,
                      borderLeft: isSelected ? `2px solid ${cfg.color}` : "2px solid transparent",
                    }}>
                      <span style={{ fontSize: 10, color: isError ? "#EF4444" : cfg.color, flexShrink: 0 }}>
                        {isError ? "✕" : cfg.icon}
                      </span>
                      <span style={{
                        fontSize: 11.5, fontFamily: "var(--font-jb-mono, monospace)",
                        color: isError ? "#EF4444" : isSelected ? "var(--text-primary)" : "var(--text-secondary)",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                        fontWeight: isSelected ? 500 : 400,
                      }}>
                        {node.label}
                      </span>
                      {isNodeRunning && (
                        <span style={{
                          width: 5, height: 5, borderRadius: "50%", background: "#10B981",
                          flexShrink: 0, animation: "pulse-ring 1.6s ease-in-out infinite",
                        }} />
                      )}
                    </div>

                    {/* Bar track */}
                    <div style={{ width: chartAreaPx, flexShrink: 0, height: ROW_H, position: "relative" }}>
                      <div style={{
                        position: "absolute",
                        left: leftPx,
                        width: widthPx,
                        top: 6, bottom: 6,
                        borderRadius: 3,
                        background: isError ? "rgba(239,68,68,0.7)" : isHovered ? cfg.color : `${cfg.color}BB`,
                        transition: "left 0.2s ease, width 0.2s ease",
                        minWidth: 4,
                        boxShadow: isNodeRunning ? `0 0 6px ${cfg.color}66` : "none",
                      }} />
                      {isNodeRunning && (
                        <div style={{
                          position: "absolute",
                          left: leftPx + widthPx,
                          width: 3, top: 6, bottom: 6,
                          background: cfg.color,
                          borderRadius: "0 3px 3px 0",
                          animation: "pulse-ring 1s ease-in-out infinite",
                          transition: "left 0.2s ease",
                        }} />
                      )}
                    </div>

                    {/* Duration — sticky right */}
                    <div style={{
                      width: DURATION_W, flexShrink: 0,
                      display: "flex", alignItems: "center", justifyContent: "flex-end",
                      paddingRight: 14,
                      fontSize: 10.5, fontFamily: "var(--font-jb-mono, monospace)",
                      color: isError ? "#EF4444" : "var(--text-dim)",
                      position: "sticky", right: 0, zIndex: 3,
                      background: rowBg,
                    }}>
                      {isNodeRunning ? "…" : formatDuration(node.durationMs)}
                    </div>
                  </div>
                </div>
              );
            })}

            {nodes.length === 0 && (
              <div style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                height: 120, color: "var(--text-dim)", fontSize: 12,
                fontFamily: "var(--font-jb-mono, monospace)", gap: 8,
              }}>
                <span style={{ opacity: 0.4 }}>◌</span>
                Waiting for events…
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Tooltip — fixed to viewport */}
      {tooltip && (() => {
        const TOOLTIP_W = 180;
        const OFFSET_X = 14;
        const OFFSET_Y = -10;
        const flipLeft = tooltip.x + OFFSET_X + TOOLTIP_W > (typeof window !== "undefined" ? window.innerWidth : 9999);
        const left = flipLeft ? tooltip.x - TOOLTIP_W - OFFSET_X : tooltip.x + OFFSET_X;
        const top = tooltip.y + OFFSET_Y;
        return (
          <div
            ref={tooltipRef}
            style={{
              position: "fixed", left, top,
              zIndex: 9999, pointerEvents: "none",
              background: "var(--bg-elevated)", border: "1px solid var(--border-strong)",
              borderRadius: 6, padding: "8px 10px", width: TOOLTIP_W,
              boxShadow: "0 4px 20px rgba(0,0,0,0.35)",
              fontSize: 11, fontFamily: "var(--font-jb-mono, monospace)",
            }}
          >
            <div style={{ fontWeight: 600, color: NODE_CONFIG[tooltip.node.type].color, marginBottom: 5 }}>
              {tooltip.node.label}
            </div>
            <div style={{ color: "var(--text-muted)", display: "flex", flexDirection: "column", gap: 3 }}>
              <div>Type: <span style={{ color: "var(--text-secondary)" }}>{NODE_CONFIG[tooltip.node.type].label}</span></div>
              <div>Start: <span style={{ color: "var(--text-secondary)" }}>+{formatDuration(tooltip.node.startOffsetMs)}</span></div>
              {tooltip.node.durationMs != null && (
                <div>Duration: <span style={{ color: "var(--text-secondary)" }}>{formatDuration(tooltip.node.durationMs)}</span></div>
              )}
              {tooltip.node.inputTokens != null && (
                <div>Tokens: <span style={{ color: "var(--text-secondary)" }}>↑{tooltip.node.inputTokens} ↓{tooltip.node.outputTokens ?? 0}</span></div>
              )}
              {tooltip.node.estimatedCostUsd != null && (
                <div>Cost: <span style={{ color: "#22C55E" }}>${tooltip.node.estimatedCostUsd.toFixed(5)}</span></div>
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ─── EventFeed ────────────────────────────────────────────────────────────────

type EventFilter = "all" | "llm" | "tool" | "thinking" | "handoff" | "error";

function EventFeed({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: TraceNode[];
  selectedId: string | null;
  onSelect: (n: TraceNode) => void;
}) {
  const [filter, setFilter] = useState<EventFilter>("all");

  const filtered = useMemo(() => {
    if (filter === "all") return nodes;
    if (filter === "error") return nodes.filter((n) => n.status === "error");
    return nodes.filter((n) => n.type === filter);
  }, [nodes, filter]);

  const errorCount = nodes.filter((n) => n.status === "error").length;

  const allFilters = [
    { key: "all" as EventFilter, label: "All", count: nodes.length },
    { key: "llm" as EventFilter, label: "LLM", count: nodes.filter((n) => n.type === "llm").length },
    { key: "tool" as EventFilter, label: "Tools", count: nodes.filter((n) => n.type === "tool").length },
    { key: "thinking" as EventFilter, label: "Think", count: nodes.filter((n) => n.type === "thinking").length },
    { key: "handoff" as EventFilter, label: "Handoff", count: nodes.filter((n) => n.type === "handoff").length },
    ...(errorCount > 0 ? [{ key: "error" as EventFilter, label: "Errors", count: errorCount }] : []),
  ];
  const filters = allFilters.filter((f) => f.count > 0 || f.key === "all");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Filter bar */}
      <div style={{
        display: "flex", gap: 2, padding: "8px 12px", borderBottom: "1px solid var(--border)",
        flexShrink: 0, overflowX: "auto",
      }}>
        {filters.map((f) => {
          const isActive = filter === f.key;
          const isError = f.key === "error";
          return (
            <button key={f.key} onClick={() => setFilter(f.key)} style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              padding: "3px 9px", borderRadius: 4, border: "none", cursor: "pointer",
              fontSize: 11, fontWeight: 500,
              background: isActive ? (isError ? "rgba(239,68,68,0.12)" : "var(--border-strong)") : "transparent",
              color: isActive ? (isError ? "#EF4444" : "var(--text-primary)") : "var(--text-muted)",
              transition: "background 0.1s, color 0.1s", whiteSpace: "nowrap",
            }}>
              {f.label}
              {f.count! > 0 && (
                <span style={{
                  fontSize: 9.5, fontFamily: "var(--font-jb-mono, monospace)",
                  color: isActive && !isError ? "var(--text-secondary)" : isError ? "#EF4444" : "var(--text-dim)",
                }}>
                  {f.count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Events list */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {filtered.map((node, idx) => {
          const cfg = NODE_CONFIG[node.type];
          const isSelected = selectedId === node.id;
          const isError = node.status === "error";
          const isRunning = node.status === "running";

          return (
            <div
              key={node.id}
              onClick={() => onSelect(node)}
              style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                padding: "8px 12px", cursor: "pointer",
                background: isSelected ? cfg.dimColor : "transparent",
                borderLeft: isSelected ? `2px solid ${cfg.color}` : "2px solid transparent",
                borderBottom: "1px solid var(--border)",
                transition: "background 0.1s",
              }}
              onMouseEnter={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = "rgba(148,163,184,0.03)"; }}
              onMouseLeave={(e) => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
            >
              {/* Icon */}
              <div style={{
                width: 24, height: 24, borderRadius: 5, flexShrink: 0,
                background: isError ? "rgba(239,68,68,0.12)" : cfg.dimColor,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 11, color: isError ? "#EF4444" : cfg.color, marginTop: 1,
              }}>
                {isError ? "✕" : cfg.icon}
              </div>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                  <span style={{ fontSize: 12, fontWeight: 500, color: isError ? "#EF4444" : "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {node.label}
                  </span>
                  <span style={{
                    fontSize: 10, color: isError ? "#EF4444" : cfg.color,
                    background: isError ? "rgba(239,68,68,0.1)" : cfg.dimColor,
                    padding: "0 5px", borderRadius: 3, flexShrink: 0,
                  }}>
                    {isError ? "error" : cfg.label.toLowerCase()}
                  </span>
                  {isRunning && <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#10B981", flexShrink: 0, animation: "pulse-ring 1.6s ease-in-out infinite" }} />}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 10.5, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-dim)" }}>
                    {formatTimestamp(node.absoluteStartIso)}
                  </span>
                  {node.durationMs != null && (
                    <span style={{ fontSize: 10.5, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-dim)" }}>
                      {formatDuration(node.durationMs)}
                    </span>
                  )}
                  {node.inputTokens != null && (
                    <span style={{ fontSize: 10.5, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-dim)" }}>
                      ↑{node.inputTokens} ↓{node.outputTokens ?? 0}
                    </span>
                  )}
                </div>
                {/* Preview */}
                <div style={{
                  marginTop: 3, fontSize: 10.5, color: "var(--text-muted)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  fontFamily: "var(--font-jb-mono, monospace)",
                }}>
                  {node.type === "llm" && node.llmContent?.response
                    ? node.llmContent.response.slice(0, 80) + (node.llmContent.response.length > 80 ? "…" : "")
                    : node.type === "tool" && node.toolContent?.input
                    ? node.toolContent.input.slice(0, 80) + (node.toolContent.input.length > 80 ? "…" : "")
                    : node.type === "thinking" && node.thinkingContent?.text
                    ? node.thinkingContent.text.slice(0, 80) + (node.thinkingContent.text.length > 80 ? "…" : "")
                    : node.type === "handoff" && node.handoffContent
                    ? `→ ${node.handoffContent.targetAgent}`
                    : null}
                </div>
              </div>

              {/* Step index */}
              <span style={{
                fontSize: 10, fontFamily: "var(--font-jb-mono, monospace)",
                color: "var(--text-dim)", flexShrink: 0, paddingTop: 3,
              }}>
                #{idx + 1}
              </span>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 80, color: "var(--text-dim)", fontSize: 12 }}>
            No events
          </div>
        )}
      </div>
    </div>
  );
}

// ─── EventDetail ─────────────────────────────────────────────────────────────

function EventDetail({ node }: { node: TraceNode | null }) {
  const cfg = node ? NODE_CONFIG[node.type] : null;

  if (!node || !cfg) {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        height: "100%", gap: 12, padding: 24,
      }}>
        <div style={{ fontSize: 28, opacity: 0.15 }}>◈</div>
        <span style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-jb-mono, monospace)", textAlign: "center" }}>
          Select an event to inspect input / output
        </span>
      </div>
    );
  }

  return (
    <div style={{ padding: "14px 16px", overflowY: "auto", height: "100%" }}>
      {/* Node header */}
      <div style={{
        display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 14,
        paddingBottom: 12, borderBottom: "1px solid var(--border)",
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 7, background: cfg.dimColor, flexShrink: 0,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 14, color: cfg.color,
        }}>
          {node.status === "error" ? "✕" : cfg.icon}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{
              fontSize: 13, fontFamily: "var(--font-jb-mono, monospace)",
              color: node.status === "error" ? "#EF4444" : "var(--text-primary)", fontWeight: 600,
            }}>
              {node.label}
            </span>
            <span style={{ fontSize: 10.5, color: cfg.color, background: cfg.dimColor, padding: "1px 7px", borderRadius: 3 }}>
              {cfg.label}
            </span>
            {node.status === "error" && (
              <span style={{ fontSize: 10.5, color: "#EF4444", background: "rgba(239,68,68,0.1)", padding: "1px 7px", borderRadius: 3 }}>
                ERROR
              </span>
            )}
          </div>
          {/* Meta row */}
          <div style={{ display: "flex", gap: 12, marginTop: 6, flexWrap: "wrap" }}>
            {[
              { label: "START", value: `+${formatDuration(node.startOffsetMs)}` },
              ...(node.durationMs != null ? [{ label: "DURATION", value: formatDuration(node.durationMs) }] : []),
              ...(node.model ? [{ label: "MODEL", value: shortModel(node.model) }] : []),
              ...(node.inputTokens != null ? [{ label: "IN", value: String(node.inputTokens) }] : []),
              ...(node.outputTokens != null ? [{ label: "OUT", value: String(node.outputTokens) }] : []),
              ...(node.estimatedCostUsd != null ? [{ label: "COST", value: `$${node.estimatedCostUsd.toFixed(5)}` }] : []),
            ].map((m) => (
              <div key={m.label}>
                <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", marginBottom: 1 }}>{m.label}</div>
                <div style={{ fontSize: 11, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-secondary)" }}>{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* LLM */}
      {node.type === "llm" && node.llmContent && (
        <>
          <ContentBlock label="PROMPT" content={node.llmContent.prompt} color={cfg.color} />
          <ContentBlock label="RESPONSE" content={node.llmContent.response} color={cfg.color} />
        </>
      )}
      {/* Tool */}
      {node.type === "tool" && node.toolContent && (
        <>
          <ContentBlock label="INPUT" content={node.toolContent.input} color={cfg.color} />
          <ContentBlock label="OUTPUT" content={node.toolContent.output || "(pending…)"} color={node.status === "error" ? "#EF4444" : cfg.color} />
        </>
      )}
      {/* Thinking */}
      {node.type === "thinking" && node.thinkingContent && (
        <ContentBlock label="REASONING" content={node.thinkingContent.text} color={cfg.color} />
      )}
      {/* Handoff */}
      {node.type === "handoff" && node.handoffContent && (
        <>
          <ContentBlock label="TARGET AGENT" content={node.handoffContent.targetAgent} color={cfg.color} />
          <ContentBlock label="MESSAGE" content={node.handoffContent.message} color={cfg.color} />
        </>
      )}

      {/* Absolute timestamp */}
      <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 5, background: "var(--border)", border: "1px solid var(--border-strong)" }}>
        <span style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: "0.07em", color: "var(--text-dim)", marginRight: 8 }}>TIMESTAMP</span>
        <span style={{ fontSize: 11, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-secondary)" }}>
          {node.absoluteStartIso.replace("T", " ").replace(/\.\d+Z$/, " UTC")}
        </span>
      </div>
    </div>
  );
}

// ─── ToolsPanel ───────────────────────────────────────────────────────────────

function ToolsPanel({ nodes }: { nodes: TraceNode[] }) {
  const toolStats = useMemo(() => {
    const map = new Map<string, { calls: number; errors: number; totalMs: number; lastStatus: string }>();
    for (const n of nodes) {
      if (n.type === "tool") {
        const e = map.get(n.label) ?? { calls: 0, errors: 0, totalMs: 0, lastStatus: "success" };
        map.set(n.label, {
          calls: e.calls + 1,
          errors: e.errors + (n.status === "error" ? 1 : 0),
          totalMs: e.totalMs + (n.durationMs ?? 0),
          lastStatus: n.status,
        });
      }
    }
    return Array.from(map.entries())
      .map(([name, s]) => ({ name, ...s, avgMs: s.calls > 0 ? s.totalMs / s.calls : 0 }))
      .sort((a, b) => b.calls - a.calls);
  }, [nodes]);

  if (toolStats.length === 0) return null;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
      gap: 8,
    }}>
      {toolStats.map((t) => {
        const hasError = t.errors > 0;
        const successRate = t.calls > 0 ? ((t.calls - t.errors) / t.calls) * 100 : 100;
        return (
          <div key={t.name} style={{
            padding: "10px 12px", borderRadius: 7,
            background: "var(--bg-elevated)", border: `1px solid ${hasError ? "rgba(239,68,68,0.15)" : "var(--border)"}`,
            display: "flex", flexDirection: "column", gap: 6,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 11, color: hasError ? "#EF4444" : "#22C55E" }}>◎</span>
              <span style={{
                fontSize: 11.5, fontFamily: "var(--font-jb-mono, monospace)", fontWeight: 500,
                color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {t.name}
              </span>
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>CALLS</div>
                <div style={{ fontSize: 13, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-primary)", fontWeight: 600 }}>{t.calls}</div>
              </div>
              <div>
                <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>AVG</div>
                <div style={{ fontSize: 13, fontFamily: "var(--font-jb-mono, monospace)", color: "var(--text-secondary)", fontWeight: 500 }}>{formatDuration(t.avgMs)}</div>
              </div>
              {t.errors > 0 && (
                <div>
                  <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>ERRORS</div>
                  <div style={{ fontSize: 13, fontFamily: "var(--font-jb-mono, monospace)", color: "#EF4444", fontWeight: 600 }}>{t.errors}</div>
                </div>
              )}
            </div>
            {/* Success bar */}
            <div style={{ height: 3, borderRadius: 2, background: "var(--border-strong)", overflow: "hidden" }}>
              <div style={{
                height: "100%", borderRadius: 2,
                width: `${successRate}%`,
                background: hasError ? "linear-gradient(90deg, #22C55E, #EF4444)" : "#22C55E",
                transition: "width 0.3s ease",
              }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionHeader({ label, count, extra }: { label: string; count?: number; extra?: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
    }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-dim)" }}>
        {label}
      </span>
      {count != null && (
        <span style={{
          fontSize: 10, fontFamily: "var(--font-jb-mono, monospace)",
          color: "var(--text-dim)", background: "var(--border)", padding: "0 5px", borderRadius: 3,
        }}>
          {count}
        </span>
      )}
      {extra && <div style={{ flex: 1, display: "flex", justifyContent: "flex-end" }}>{extra}</div>}
    </div>
  );
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function ObservabilityClient() {
  const searchParams = useSearchParams();
  const threadId = searchParams.get("id");

  const [thread, setThread] = useState<ThreadSummary | null>(null);
  const [nodes, setNodes] = useState<TraceNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [selectedNode, setSelectedNode] = useState<TraceNode | null>(null);
  const [ganttTab, setGanttTab] = useState<"all" | string>("all");
  const [liveMs, setLiveMs] = useState(0);
  const [elapsedDisplay, setElapsedDisplay] = useState("0ms");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const liveRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isRunning = thread ? normalizeStatus(thread.status) === "running" : false;

  // Fetch timeline
  const fetchTimeline = useCallback(async (currentThread: ThreadSummary) => {
    try {
      const evs = await getThreadTimeline(currentThread.thread_id);
      const built = timelineEventsToTraceNodes(evs, currentThread);
      setNodes(built);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load timeline");
    }
  }, []);

  // Initial load: fetch thread from list
  useEffect(() => {
    if (!threadId) { setLoading(false); return; }
    let cancelled = false;

    async function load() {
      try {
        const { items: threads } = await listThreads({ limit: 200 });
        const found = threads.find((t) => t.thread_id === threadId);
        if (!found) { setError(`Thread "${threadId}" not found`); setLoading(false); return; }
        if (cancelled) return;
        setThread(found);
        await fetchTimeline(found);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [threadId, fetchTimeline]);

  // Poll when running
  useEffect(() => {
    if (!thread || !isRunning) return;
    pollRef.current = setInterval(async () => {
      try {
        const { items: threads } = await listThreads({ limit: 200 });
        const updated = threads.find((t) => t.thread_id === thread.thread_id);
        if (updated) {
          setThread(updated);
          await fetchTimeline(updated);
        }
      } catch { /* silent */ }
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [thread?.thread_id, isRunning, fetchTimeline]);

  // Live timer update
  useEffect(() => {
    if (!thread) return;
    const startTs = new Date(thread.started_at).getTime();

    const update = () => {
      const fromNodes = calcTotalMs(nodes);
      if (isRunning) {
        const elapsed = Date.now() - startTs;
        const ms = Math.max(elapsed, fromNodes);
        setLiveMs(ms);
        setElapsedDisplay(formatDuration(elapsed));
      } else {
        setLiveMs(fromNodes);
        setElapsedDisplay(formatDuration(thread.duration_ms ?? fromNodes));
      }
    };

    update();
    if (isRunning) {
      liveRef.current = setInterval(update, 200);
    }
    return () => { if (liveRef.current) clearInterval(liveRef.current); };
  }, [thread, nodes, isRunning]);

  // API status
  useEffect(() => {
    checkApiStatus().then(setApiStatus);
    const iv = setInterval(() => checkApiStatus().then(setApiStatus), 15000);
    return () => clearInterval(iv);
  }, []);

  // Cost estimate
  const totalCost = useMemo(() => {
    return nodes.reduce((acc, n) => acc + (n.estimatedCostUsd ?? 0), 0);
  }, [nodes]);

  // Errors
  const errorNodes = useMemo(() => nodes.filter((n) => n.status === "error"), [nodes]);

  // ── Render ──

  if (!threadId) {
    return (
      <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "var(--bg-base)" }}>
        <ObservabilityNav thread={null} apiStatus={apiStatus} isLive={false} />
        <div style={{ paddingTop: 48, display: "flex", alignItems: "center", justifyContent: "center", flex: 1 }}>
          <div style={{ textAlign: "center", color: "var(--text-muted)", fontSize: 14 }}>
            <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.3 }}>◌</div>
            No thread ID provided. Open from a thread row.
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "var(--bg-base)" }}>
        <ObservabilityNav thread={null} apiStatus={apiStatus} isLive={false} />
        <div style={{ paddingTop: 48, display: "flex", alignItems: "center", justifyContent: "center", flex: 1 }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
            <div style={{ width: 22, height: 22, borderRadius: "50%", border: "2px solid var(--border-strong)", borderTopColor: "var(--accent-blue)", animation: "spin 0.9s linear infinite" }} />
            <span style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-jb-mono, monospace)" }}>Loading thread…</span>
          </div>
        </div>
      </div>
    );
  }

  if (error || !thread) {
    return (
      <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "var(--bg-base)" }}>
        <ObservabilityNav thread={null} apiStatus={apiStatus} isLive={false} />
        <div style={{ paddingTop: 48, display: "flex", alignItems: "center", justifyContent: "center", flex: 1 }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 20, color: "#EF4444", marginBottom: 8 }}>✕</div>
            <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>{error ?? "Thread not found"}</div>
            <a href="/dashboard/threads" style={{ marginTop: 12, display: "inline-block", fontSize: 12, color: "var(--accent-blue)" }}>← Back to Threads</a>
          </div>
        </div>
      </div>
    );
  }

  const agents = thread.agents && thread.agents.length > 0 ? thread.agents : [thread.agent_name || thread.agent_id];

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh", background: "var(--bg-base)" }}>
      <ObservabilityNav thread={thread} apiStatus={apiStatus} isLive={isRunning} />

      <main style={{ paddingTop: 48, flex: 1 }}>

        {/* ── Thread header ── */}
        <div style={{
          padding: "16px 24px 0",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-surface)",
        }}>
          {/* Top row: agent flow + status + trigger */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
            {agents.length > 0 && <AgentFlow agents={agents} />}
            <StatusBadge status={thread.status} size="md" />
            {thread.trigger && <TriggerChip trigger={thread.trigger} />}
            {thread.budget_soft_warned && thread.status !== "budget_exceeded" && (
              <span
                title={`Budget alert: $${thread.run_cost_usd?.toFixed(4)} spent (warn threshold $${thread.budget_usd?.toFixed(4)})`}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 3,
                  fontSize: 11, fontWeight: 500, color: "#FB923C",
                  background: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.25)",
                  borderRadius: 5, padding: "2px 6px",
                }}
              >
                ⚠ Budget Alert
              </span>
            )}
            {thread.rate_limited && (
              <span
                title={`Rate limited ${thread.rate_limit_waits}x during this run`}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 3,
                  fontSize: 11, fontWeight: 500, color: "#A78BFA",
                  background: "rgba(139,92,246,0.1)", border: "1px solid rgba(139,92,246,0.25)",
                  borderRadius: 5, padding: "2px 6px",
                }}
              >
                ⏱ Rate limited{thread.rate_limit_waits > 1 ? ` (${thread.rate_limit_waits}x)` : ""}
              </span>
            )}
          </div>

          {/* Stat line */}
          <div style={{ paddingBottom: 16 }}>
            <StatLine items={[
              { label: "Duration", value: elapsedDisplay, accent: isRunning ? "#10B981" : undefined },
              { label: "Tokens", value: formatTokens(thread.tokens_used) },
              { label: "Steps", value: String(thread.step_index) },
              { label: "LLM calls", value: String(nodes.filter((n) => n.type === "llm").length) },
              { label: "Tool calls", value: String(nodes.filter((n) => n.type === "tool").length) },
              { label: "Model", value: shortModel(thread.model) },
              { label: "Started", value: formatAbsTime(thread.started_at) },
              ...(totalCost > 0 ? [{ label: "Est. cost", value: `$${totalCost.toFixed(5)}`, accent: "#22C55E" }] : []),
              ...(errorNodes.length > 0 ? [{ label: "Errors", value: String(errorNodes.length), accent: "#EF4444" }] : []),
            ]} />
          </div>
        </div>

        {/* ── Error banner ── */}
        {errorNodes.length > 0 && (
          <div style={{
            margin: "16px 24px 0", padding: "10px 14px", borderRadius: 7,
            background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)",
            display: "flex", alignItems: "flex-start", gap: 10,
          }}>
            <span style={{ fontSize: 14, color: "#EF4444", flexShrink: 0, marginTop: 1 }}>⚠</span>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#EF4444", marginBottom: 3 }}>
                {errorNodes.length} tool error{errorNodes.length > 1 ? "s" : ""} detected
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {errorNodes.map((n) => (
                  <button key={n.id} onClick={() => setSelectedNode(n)} style={{
                    fontSize: 10.5, fontFamily: "var(--font-jb-mono, monospace)", padding: "2px 8px",
                    borderRadius: 4, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)",
                    color: "#EF4444", cursor: "pointer",
                  }}>
                    {n.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Gantt timeline ── */}
        {(() => {
          // Unique agent IDs in order of first appearance
          const agentIds = Array.from(
            new Map(nodes.map((n) => [n.agentId, n.agentId])).keys()
          );
          const isMultiAgent = agentIds.length > 1;
          const visibleNodes = ganttTab === "all"
            ? nodes
            : nodes.filter((n) => n.agentId === ganttTab);

          return (
            <div style={{ margin: "16px 24px 0", borderRadius: 8, background: "var(--bg-surface)", border: "1px solid var(--border)", overflow: "hidden" }}>
              {/* Tab bar */}
              <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                borderBottom: "1px solid var(--border)", paddingInline: 16, paddingTop: 12,
              }}>
                {/* Tabs */}
                <div style={{ display: "flex", gap: 0 }}>
                  {/* Overview tab */}
                  {[
                    { key: "all" as const, label: "Overview", count: nodes.length },
                    ...(isMultiAgent ? agentIds.map((id) => ({
                      key: id,
                      label: id,
                      count: nodes.filter((n) => n.agentId === id).length,
                    })) : []),
                  ].map((tab) => {
                    const isActive = ganttTab === tab.key;
                    return (
                      <button
                        key={tab.key}
                        onClick={() => setGanttTab(tab.key)}
                        style={{
                          display: "inline-flex", alignItems: "center", gap: 6,
                          padding: "7px 14px", fontSize: 12, fontWeight: isActive ? 500 : 400,
                          color: isActive ? "var(--text-primary)" : "var(--text-muted)",
                          background: "transparent", border: "none", cursor: "pointer",
                          borderBottom: isActive ? "2px solid var(--accent-blue)" : "2px solid transparent",
                          marginBottom: -1, transition: "color 0.12s, border-color 0.12s",
                          fontFamily: tab.key !== "all" ? "var(--font-jb-mono, monospace)" : undefined,
                        }}
                      >
                        {tab.label}
                        <span style={{
                          fontSize: 10, fontFamily: "var(--font-jb-mono, monospace)",
                          color: isActive ? "var(--text-secondary)" : "var(--text-dim)",
                          background: "var(--border)", padding: "0 5px", borderRadius: 3,
                        }}>
                          {tab.count}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Legend */}
                <div style={{ display: "flex", alignItems: "center", gap: 12, paddingBottom: 8 }}>
                  {Object.entries(NODE_CONFIG).map(([type, cfg]) => (
                    <div key={type} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10.5, color: "var(--text-muted)" }}>
                      <span style={{ fontSize: 9, color: cfg.color }}>{cfg.icon}</span>
                      {cfg.label}
                    </div>
                  ))}
                  {isRunning && (
                    <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10.5, color: "#10B981" }}>
                      <div style={{ width: 2, height: 10, background: "#10B981", borderRadius: 1 }} />
                      Now
                    </div>
                  )}
                </div>
              </div>

              {/* Chart */}
              <div style={{ padding: "12px 16px 16px" }}>
                <GanttChart
                  nodes={visibleNodes}
                  thread={thread}
                  liveMs={liveMs}
                  selectedId={selectedNode?.id ?? null}
                  onSelect={setSelectedNode}
                />
              </div>
            </div>
          );
        })()}

        {/* ── Bottom split: Event Feed + Detail ── */}
        {/* calc: 100vh minus nav (48px) minus top-header (~140px) minus gantt section minus margins */}
        <div style={{
          margin: "16px 24px 0",
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 16,
          alignItems: "start",
        }}>
          {/* Event Feed — fixed height with inner scroll */}
          <div style={{
            borderRadius: 8, background: "var(--bg-surface)", border: "1px solid var(--border)",
            display: "flex", flexDirection: "column",
            height: "calc(100vh - 48px - 160px)",
            minHeight: 320,
            overflow: "hidden",
          }}>
            <div style={{ padding: "12px 14px 0", flexShrink: 0 }}>
              <SectionHeader label="EVENTS" count={nodes.length} />
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
              <EventFeed nodes={nodes} selectedId={selectedNode?.id ?? null} onSelect={setSelectedNode} />
            </div>
          </div>

          {/* Event Detail — sticky, same height cap */}
          <div style={{
            position: "sticky",
            top: 48 + 12,
            borderRadius: 8, background: "var(--bg-surface)", border: "1px solid var(--border)",
            display: "flex", flexDirection: "column",
            height: "calc(100vh - 48px - 160px)",
            minHeight: 320,
            overflow: "hidden",
          }}>
            <div style={{ padding: "12px 14px", flexShrink: 0, borderBottom: "1px solid var(--border)" }}>
              <SectionHeader
                label={selectedNode ? `${NODE_CONFIG[selectedNode.type].label.toUpperCase()} DETAIL` : "EVENT DETAIL"}
              />
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
              <EventDetail node={selectedNode} />
            </div>
          </div>
        </div>

        {/* ── Tools ── */}
        {nodes.some((n) => n.type === "tool") && (
          <div style={{ margin: "16px 24px 24px", padding: "16px", borderRadius: 8, background: "var(--bg-surface)", border: "1px solid var(--border)" }}>
            <SectionHeader
              label="CONNECTED TOOLS"
              count={new Set(nodes.filter((n) => n.type === "tool").map((n) => n.label)).size}
            />
            <ToolsPanel nodes={nodes} />
          </div>
        )}
      </main>
    </div>
  );
}
