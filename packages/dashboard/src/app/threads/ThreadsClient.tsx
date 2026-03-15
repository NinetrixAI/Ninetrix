"use client";

import { useState, useEffect, useCallback, useRef } from "react";
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

// ─── Helpers ────────────────────────────────────────────────────────────────

const JSON_THEME: PrismTheme = {
  plain: { backgroundColor: "transparent", color: "#8A95A8" },
  styles: [
    { types: ["property"], style: { color: "#60A5FA" } },         // keys — blue
    { types: ["string"], style: { color: "#4ADE80" } },            // string values — green
    { types: ["number", "boolean", "null", "keyword"], style: { color: "#4ADE80" } },
    { types: ["punctuation", "operator"], style: { color: "#4B5563" } },
  ],
};

function formatDuration(ms: number | null): string {
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
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function truncId(id: string, len = 8): string {
  return id.length > len ? id.slice(0, len) : id;
}

function normalizeStatus(s: string): "running" | "completed" | "error" | "pending" | "cancelled" {
  if (s === "in_progress" || s === "started" || s === "running") return "running";
  if (s === "completed" || s === "approved") return "completed";
  if (s === "error" || s === "failed") return "error";
  if (s === "waiting_for_approval" || s === "pending") return "pending";
  return "cancelled";
}

const MODEL_SHORT: Record<string, string> = {
  "claude-opus": "Opus",
  "claude-sonnet": "Sonnet",
  "claude-haiku": "Haiku",
  "gpt-4o": "GPT-4o",
  "gpt-4": "GPT-4",
  "gpt-3.5": "GPT-3.5",
};

function shortModel(model: string): string {
  for (const [key, val] of Object.entries(MODEL_SHORT)) {
    if (model.toLowerCase().includes(key)) return val;
  }
  return model.split("-").slice(-1)[0] || model;
}

// ─── Status Badge ────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  running: { bg: "rgba(16,185,129,0.1)", text: "#10B981", dot: "#10B981", label: "Running" },
  completed: { bg: "rgba(100,116,139,0.1)", text: "#94A3B8", dot: "#64748B", label: "Completed" },
  error: { bg: "rgba(239,68,68,0.1)", text: "#EF4444", dot: "#EF4444", label: "Failed" },
  pending: { bg: "rgba(245,158,11,0.1)", text: "#F59E0B", dot: "#F59E0B", label: "Pending" },
  cancelled: { bg: "rgba(100,116,139,0.08)", text: "#64748B", dot: "#475569", label: "Cancelled" },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[normalizeStatus(status)] ?? STATUS_STYLES.cancelled;
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

// ─── Trigger chip ────────────────────────────────────────────────────────────

const TRIGGER_ICONS: Record<string, string> = {
  webhook: "⚡",
  schedule: "⏱",
  api: "⬡",
  manual: "▶",
  github: "◆",
};

function TriggerChip({ trigger }: { trigger: string }) {
  const t = trigger?.toLowerCase() || "api";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 7px",
        borderRadius: 3,
        background: "var(--border)",
        color: "var(--text-secondary)",
        fontSize: 11,
        fontWeight: 500,
        letterSpacing: "0.03em",
        border: "1px solid var(--border-strong)",
      }}
    >
      {TRIGGER_ICONS[t] ?? "○"}{" "}
      {t.charAt(0).toUpperCase() + t.slice(1)}
    </span>
  );
}

// ─── Node type config ────────────────────────────────────────────────────────

const NODE_CONFIG = {
  llm: { color: "#3B82F6", dimColor: "rgba(59,130,246,0.12)", icon: "◈", label: "LLM" },
  tool: { color: "#22C55E", dimColor: "rgba(34,197,94,0.12)", icon: "◎", label: "Tool" },
  thinking: { color: "#F59E0B", dimColor: "rgba(245,158,11,0.12)", icon: "◉", label: "Think" },
  handoff: { color: "#8B5CF6", dimColor: "rgba(139,92,246,0.12)", icon: "◀▶", label: "Handoff" },
};

// ─── Top Navigation ──────────────────────────────────────────────────────────

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
          { label: "Threads", active: true, href: "/dashboard/threads" },
          { label: "Agents", active: false, href: "/dashboard/agents" },
          { label: "Settings", active: false, href: "#" },
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

        {/* Theme toggle */}
        <ThemeToggle />
      </div>
    </nav>
  );
}

// ─── Content block renderer ───────────────────────────────────────────────────

function ContentBlock({
  label,
  content,
  color,
}: {
  label: string;
  content: string;
  color: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const preview = content.length > 120 ? content.slice(0, 120) + "…" : content;

  return (
    <div
      style={{
        marginTop: 8,
        borderRadius: 6,
        border: `1px solid ${color}22`,
        background: `${color}08`,
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setExpanded((e) => !e)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "6px 10px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          color: "#8A95A8",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textAlign: "left",
        }}
      >
        <span style={{ color }}>{label}</span>
        <span style={{ fontSize: 10, opacity: 0.5 }}>{expanded ? "▲" : "▼"}</span>
      </button>
      {!expanded && (
        <p
          style={{
            margin: 0,
            padding: "0 10px 8px",
            fontSize: 11,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: "#8A95A8",
            lineHeight: 1.6,
            opacity: 0.7,
          }}
        >
          {preview}
        </p>
      )}
      {expanded && (
        <pre
          style={{
            margin: 0,
            padding: "0 10px 10px",
            fontSize: 11,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: "var(--text-primary)",
            lineHeight: 1.7,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {content || "(empty)"}
        </pre>
      )}
    </div>
  );
}

// ─── Trace Node Row ───────────────────────────────────────────────────────────

function TraceNodeRow({
  node,
  totalMs,
  onSelect,
  isSelected,
}: {
  node: TraceNode;
  totalMs: number;
  onSelect: (n: TraceNode) => void;
  isSelected: boolean;
}) {
  const cfg = NODE_CONFIG[node.type];
  const [expanded, setExpanded] = useState(false);

  const handleClick = () => {
    setExpanded((e) => !e);
    onSelect(node);
  };

  return (
    <div
      style={{
        marginLeft: node.depth * 20,
        marginBottom: 2,
      }}
    >
      <div
        onClick={handleClick}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "7px 10px",
          borderRadius: 6,
          cursor: "pointer",
          background: isSelected ? `${cfg.color}0D` : "transparent",
          border: isSelected
            ? `1px solid ${cfg.color}25`
            : "1px solid transparent",
          transition: "background 0.12s, border-color 0.12s",
        }}
        onMouseEnter={(e) => {
          if (!isSelected)
            (e.currentTarget as HTMLDivElement).style.background =
              "rgba(148,163,184,0.04)";
        }}
        onMouseLeave={(e) => {
          if (!isSelected)
            (e.currentTarget as HTMLDivElement).style.background = "transparent";
        }}
      >
        {/* Left border indicator */}
        <div
          style={{
            width: 2,
            alignSelf: "stretch",
            borderRadius: 1,
            background: cfg.color,
            flexShrink: 0,
            opacity: 0.6,
          }}
        />

        {/* Icon */}
        <span
          style={{
            fontSize: 12,
            color: cfg.color,
            width: 16,
            textAlign: "center",
            flexShrink: 0,
          }}
        >
          {node.status === "running" ? (
            <span className="animate-spin" style={{ display: "inline-block" }}>
              ◌
            </span>
          ) : node.status === "error" ? (
            <span style={{ color: "#EF4444" }}>✕</span>
          ) : (
            cfg.icon
          )}
        </span>

        {/* Label */}
        <span
          style={{
            flex: 1,
            fontSize: 12.5,
            fontWeight: 500,
            color: "var(--text-primary)",
            fontFamily: "var(--font-jb-mono, monospace)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {node.label}
        </span>

        {/* Meta: tokens */}
        {node.inputTokens != null && (
          <span
            style={{
              fontSize: 10.5,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: "var(--text-muted)",
              whiteSpace: "nowrap",
            }}
          >
            ↑{node.inputTokens} ↓{node.outputTokens}
          </span>
        )}

        {/* Meta: duration */}
        <span
          style={{
            fontSize: 10.5,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: "var(--text-muted)",
            whiteSpace: "nowrap",
            minWidth: 40,
            textAlign: "right",
          }}
        >
          {node.durationMs != null
            ? formatDuration(node.durationMs)
            : node.status === "running"
            ? "…"
            : "—"}
        </span>

        {/* Expand chevron */}
        <span
          style={{
            fontSize: 9,
            color: "var(--text-dim)",
            marginLeft: 2,
            transform: expanded ? "rotate(180deg)" : "none",
            transition: "transform 0.15s",
          }}
        >
          ▼
        </span>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div
          style={{
            marginLeft: 26,
            marginTop: 2,
            marginBottom: 6,
            paddingLeft: 10,
            borderLeft: `2px solid ${cfg.color}20`,
          }}
        >
          {/* LLM content */}
          {node.llmContent && (
            <>
              {node.llmContent.prompt && (
                <ContentBlock
                  label="PROMPT"
                  content={node.llmContent.prompt}
                  color="#3B82F6"
                />
              )}
              <ContentBlock
                label="RESPONSE"
                content={node.llmContent.response}
                color="#22C55E"
              />
              {node.estimatedCostUsd != null && (
                <div
                  style={{
                    marginTop: 6,
                    padding: "4px 10px",
                    display: "flex",
                    gap: 16,
                    fontSize: 11,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "#4B5563",
                  }}
                >
                  <span>
                    Model:{" "}
                    <span style={{ color: "#8A95A8" }}>{node.model ?? "—"}</span>
                  </span>
                  <span>
                    In:{" "}
                    <span style={{ color: "#8A95A8" }}>
                      {node.inputTokens} tok
                    </span>
                  </span>
                  <span>
                    Out:{" "}
                    <span style={{ color: "#8A95A8" }}>
                      {node.outputTokens} tok
                    </span>
                  </span>
                  <span>
                    Cost:{" "}
                    <span style={{ color: "#8A95A8" }}>
                      ${node.estimatedCostUsd.toFixed(4)}
                    </span>
                  </span>
                </div>
              )}
            </>
          )}

          {/* Tool content */}
          {node.toolContent && (
            <>
              <ContentBlock
                label="INPUT"
                content={node.toolContent.input}
                color="#22C55E"
              />
              {node.toolContent.output && (
                <ContentBlock
                  label="OUTPUT"
                  content={node.toolContent.output}
                  color="#06B6D4"
                />
              )}
            </>
          )}

          {/* Handoff content */}
          {node.handoffContent && (
            <>
              <div
                style={{
                  marginTop: 8,
                  padding: "8px 12px",
                  borderRadius: 6,
                  background: "rgba(139,92,246,0.08)",
                  border: "1px solid rgba(139,92,246,0.15)",
                }}
              >
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: "#8B5CF6",
                    letterSpacing: "0.06em",
                    marginBottom: 4,
                  }}
                >
                  HANDOFF TO
                </div>
                <div
                  style={{
                    fontSize: 13,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "#E2E8F0",
                    fontWeight: 600,
                  }}
                >
                  {node.handoffContent.targetAgent}
                </div>
              </div>
              {node.handoffContent.message && (
                <ContentBlock
                  label="MESSAGE"
                  content={node.handoffContent.message}
                  color="#8B5CF6"
                />
              )}
            </>
          )}

          {/* Thinking content */}
          {node.thinkingContent && (
            <ContentBlock
              label="REASONING"
              content={node.thinkingContent.text}
              color="#F59E0B"
            />
          )}
        </div>
      )}
    </div>
  );
}

// ─── Timeline / Gantt ────────────────────────────────────────────────────────

function TimelineView({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: TraceNode[];
  selectedId: string | null;
  onSelect: (node: TraceNode) => void;
}) {
  const totalMs = calcTotalMs(nodes);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const tickCount = 6;
  const ticks = Array.from({ length: tickCount }, (_, i) =>
    Math.round((totalMs / (tickCount - 1)) * i)
  );

  return (
    <div style={{ padding: "12px 0" }}>
      {/* Time ruler */}
      <div
        style={{
          display: "flex",
          marginBottom: 8,
          paddingLeft: 160,
          paddingRight: 16,
        }}
      >
        {ticks.map((t) => (
          <div
            key={t}
            style={{
              flex: 1,
              fontSize: 9.5,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: "var(--text-dim)",
              textAlign: t === 0 ? "left" : t === totalMs ? "right" : "center",
            }}
          >
            {t < 1000 ? `${t}ms` : `${(t / 1000).toFixed(1)}s`}
          </div>
        ))}
      </div>

      {/* Grid + bars */}
      <div style={{ position: "relative" }}>
        {/* Vertical grid lines */}
        {ticks.slice(1, -1).map((t, i) => (
          <div
            key={i}
            style={{
              position: "absolute",
              left: `calc(160px + (100% - 176px) * ${t / totalMs})`,
              top: 0,
              bottom: 0,
              width: 1,
              background: "rgba(148,163,184,0.05)",
              zIndex: 0,
              pointerEvents: "none",
            }}
          />
        ))}

        {/* Rows */}
        {nodes.map((node) => {
          const cfg = NODE_CONFIG[node.type];
          const left = (node.startOffsetMs / totalMs) * 100;
          const width = node.durationMs
            ? Math.max(0.5, (node.durationMs / totalMs) * 100)
            : 1;
          const isHovered = hoveredId === node.id;
          const isSelected = selectedId === node.id;

          return (
            <div
              key={node.id}
              onMouseEnter={() => setHoveredId(node.id)}
              onMouseLeave={() => setHoveredId(null)}
              onClick={() => onSelect(node)}
              style={{
                display: "flex",
                alignItems: "center",
                marginBottom: 3,
                paddingLeft: node.depth * 12,
                background: isSelected
                  ? `${cfg.dimColor}`
                  : isHovered
                  ? "rgba(148,163,184,0.04)"
                  : "transparent",
                borderRadius: 4,
                cursor: "pointer",
                borderLeft: isSelected ? `2px solid ${cfg.color}` : "2px solid transparent",
                transition: "background 0.1s",
              }}
            >
              {/* Label column */}
              <div
                style={{
                  width: 160 - node.depth * 12,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  paddingRight: 10,
                }}
              >
                <span style={{ fontSize: 10, color: cfg.color }}>{cfg.icon}</span>
                <span
                  style={{
                    fontSize: 11,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {node.label}
                </span>
              </div>

              {/* Bar track */}
              <div
                style={{
                  flex: 1,
                  height: 20,
                  position: "relative",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    left: `${left}%`,
                    width: `${width}%`,
                    top: 2,
                    bottom: 2,
                    borderRadius: 3,
                    background: isHovered ? cfg.color : `${cfg.color}BB`,
                    transition: "background 0.1s",
                    minWidth: 3,
                  }}
                />
              </div>

              {/* Duration */}
              <div
                style={{
                  width: 56,
                  textAlign: "right",
                  fontSize: 10,
                  fontFamily: "var(--font-jb-mono, monospace)",
                  color: "var(--text-dim)",
                  paddingRight: 16,
                  flexShrink: 0,
                }}
              >
                {formatDuration(node.durationMs)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Timeline Detail Panel ───────────────────────────────────────────────────

function TimelineDetailPanel({ node }: { node: TraceNode | null }) {
  const cfg = node ? NODE_CONFIG[node.type] : null;

  if (!node || !cfg) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--text-dim)",
          fontSize: 12,
          fontFamily: "var(--font-jb-mono, monospace)",
          gap: 8,
        }}
      >
        <span style={{ opacity: 0.4 }}>↑</span>
        Click a row to inspect input / output
      </div>
    );
  }

  return (
    <div style={{ padding: "12px 16px", height: "100%", overflowY: "auto" }}>
      {/* Node header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
          paddingBottom: 10,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span style={{ fontSize: 12, color: cfg.color }}>{cfg.icon}</span>
        <span
          style={{
            fontSize: 12,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: "var(--text-primary)",
            fontWeight: 600,
          }}
        >
          {node.label}
        </span>
        <span
          style={{
            fontSize: 10,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: cfg.color,
            background: cfg.dimColor,
            padding: "1px 7px",
            borderRadius: 3,
          }}
        >
          {cfg.label}
        </span>
        <div style={{ flex: 1 }} />
        {node.durationMs != null && (
          <span
            style={{
              fontSize: 10,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: "var(--text-dim)",
            }}
          >
            {formatDuration(node.durationMs)}
          </span>
        )}
        {(node.inputTokens != null || node.outputTokens != null) && (
          <span
            style={{
              fontSize: 10,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: "var(--text-dim)",
            }}
          >
            {node.inputTokens != null && `↑${node.inputTokens}`}
            {node.inputTokens != null && node.outputTokens != null && " "}
            {node.outputTokens != null && `↓${node.outputTokens}`}
          </span>
        )}
      </div>

      {/* LLM content */}
      {node.type === "llm" && node.llmContent && (
        <>
          <ContentBlock label="PROMPT" content={node.llmContent.prompt} color={cfg.color} />
          <ContentBlock label="RESPONSE" content={node.llmContent.response} color={cfg.color} />
        </>
      )}

      {/* Tool content */}
      {node.type === "tool" && node.toolContent && (
        <>
          <ContentBlock label="INPUT" content={node.toolContent.input} color={cfg.color} />
          <ContentBlock label="OUTPUT" content={node.toolContent.output} color={cfg.color} />
        </>
      )}

      {/* Thinking content */}
      {node.type === "thinking" && node.thinkingContent && (
        <ContentBlock label="REASONING" content={node.thinkingContent.text} color={cfg.color} />
      )}

      {/* Handoff content */}
      {node.type === "handoff" && node.handoffContent && (
        <>
          <ContentBlock label="HANDOFF TO" content={node.handoffContent.targetAgent} color={cfg.color} />
          <ContentBlock label="MESSAGE" content={node.handoffContent.message} color={cfg.color} />
        </>
      )}

      {/* Metadata */}
      {node.model && (
        <div
          style={{
            marginTop: 12,
            display: "flex",
            gap: 16,
            flexWrap: "wrap",
          }}
        >
          {[
            { label: "MODEL", value: node.model },
            node.estimatedCostUsd != null
              ? { label: "EST. COST", value: `$${node.estimatedCostUsd.toFixed(5)}` }
              : null,
            { label: "STARTED", value: node.absoluteStartIso.replace("T", " ").replace("Z", "") },
          ]
            .filter(Boolean)
            .map((s) => s && (
              <div key={s.label}>
                <div
                  style={{
                    fontSize: 9,
                    fontWeight: 600,
                    letterSpacing: "0.07em",
                    color: "var(--text-dim)",
                    marginBottom: 2,
                  }}
                >
                  {s.label}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {s.value}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

// ─── Trace Drawer ────────────────────────────────────────────────────────────

function TraceDrawer({
  thread,
  onClose,
}: {
  thread: ThreadSummary;
  onClose: () => void;
}) {
  const [view, setView] = useState<"trace" | "timeline" | "raw">("trace");
  const [nodes, setNodes] = useState<TraceNode[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<TraceNode | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Resizable split for timeline view
  const [topHeight, setTopHeight] = useState(280);
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);
  const splitContainerRef = useRef<HTMLDivElement | null>(null);

  const onDividerMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: topHeight };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current || !splitContainerRef.current) return;
      const containerH = splitContainerRef.current.clientHeight;
      const delta = ev.clientY - dragRef.current.startY;
      const next = Math.min(
        Math.max(dragRef.current.startH + delta, 80),
        containerH - 80
      );
      setTopHeight(next);
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const fetchTimeline = useCallback(async () => {
    try {
      const evs = await getThreadTimeline(thread.thread_id);
      setEvents(evs);
      setNodes(timelineEventsToTraceNodes(evs, thread));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load timeline");
    } finally {
      setLoading(false);
    }
  }, [thread]);

  useEffect(() => {
    setLoading(true);
    setNodes([]);
    setEvents([]);
    setSelectedNode(null);
    fetchTimeline();

    // Poll if running
    if (normalizeStatus(thread.status) === "running") {
      pollRef.current = setInterval(fetchTimeline, 3000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [thread.thread_id, fetchTimeline, thread.status]);

  const totalTokens = formatTokens(thread.tokens_used);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 190,
          background: "rgba(0,0,0,0.45)",
        }}
      />

      {/* Drawer panel */}
      <div
        className="animate-slide-in"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: 700,
          maxWidth: "92vw",
          zIndex: 200,
          background: "var(--bg-surface)",
          borderLeft: "1px solid var(--border-strong)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "-8px 0 32px rgba(0,0,0,0.2)",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "14px 18px 12px",
            borderBottom: "1px solid var(--border)",
            flexShrink: 0,
            background: "var(--bg-elevated)",
          }}
        >
          {/* Row 1: ID + close */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 10,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <code
                style={{
                  fontSize: 11,
                  fontFamily: "var(--font-jb-mono, monospace)",
                  color: "var(--text-secondary)",
                  background: "var(--border)",
                  padding: "2px 8px",
                  borderRadius: 4,
                  border: "1px solid var(--border-strong)",
                }}
              >
                {thread.thread_id}
              </code>
              <StatusBadge status={thread.status} />
              {thread.trigger && <TriggerChip trigger={thread.trigger} />}
            </div>

            <button
              onClick={onClose}
              style={{
                width: 28,
                height: 28,
                borderRadius: 6,
                border: "1px solid var(--border-strong)",
                background: "transparent",
                color: "var(--text-muted)",
                cursor: "pointer",
                fontSize: 16,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              ✕
            </button>
          </div>

          {/* Row 2: Stats */}
          <div
            style={{
              display: "flex",
              gap: 20,
              flexWrap: "wrap",
            }}
          >
            {[
              { label: "Agent", value: thread.agent_name || thread.agent_id },
              { label: "Model", value: thread.model },
              { label: "Steps", value: String(thread.step_index) },
              { label: "Tokens", value: totalTokens },
              { label: "Duration", value: formatDuration(thread.duration_ms) },
              { label: "Started", value: formatAbsTime(thread.started_at) },
            ].map((stat) => (
              <div key={stat.label}>
                <div
                  style={{
                    fontSize: 9.5,
                    fontWeight: 600,
                    letterSpacing: "0.07em",
                    color: "var(--text-dim)",
                    marginBottom: 2,
                  }}
                >
                  {stat.label}
                </div>
                <div
                  style={{
                    fontSize: 12,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-primary)",
                    fontWeight: 500,
                  }}
                >
                  {stat.value}
                </div>
              </div>
            ))}
          </div>

          {/* Row 3: Multi-agent list */}
          {thread.agents && thread.agents.length > 1 && (
            <div
              style={{
                marginTop: 10,
                display: "flex",
                alignItems: "center",
                gap: 6,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 600, letterSpacing: "0.06em" }}>
                AGENTS
              </span>
              {thread.agents.map((a, i) => (
                <span key={a}>
                  <span
                    style={{
                      fontSize: 11,
                      fontFamily: "var(--font-jb-mono, monospace)",
                      color: "var(--text-secondary)",
                      background: "var(--border)",
                      padding: "1px 7px",
                      borderRadius: 3,
                      border: "1px solid var(--border-strong)",
                    }}
                  >
                    {a}
                  </span>
                  {i < thread.agents.length - 1 && (
                    <span
                      style={{ fontSize: 10, color: "var(--text-dim)", marginLeft: 4 }}
                    >
                      →
                    </span>
                  )}
                </span>
              ))}
            </div>
          )}

          {/* View toggle */}
          <div
            style={{
              marginTop: 12,
              display: "flex",
              gap: 2,
              background: "var(--border)",
              padding: 3,
              borderRadius: 7,
              width: "fit-content",
              border: "1px solid var(--border-strong)",
            }}
          >
            {(["trace", "timeline", "raw"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                style={{
                  padding: "4px 14px",
                  borderRadius: 5,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 500,
                  background: view === v ? "var(--bg-surface)" : "transparent",
                  color: view === v ? "var(--text-primary)" : "var(--text-muted)",
                  transition: "background 0.12s, color 0.12s",
                }}
              >
                {v.charAt(0).toUpperCase() + v.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Body */}
        {view === "timeline" ? (
          // ── Split pane layout for timeline tab ──────────────────────────────
          <div
            ref={splitContainerRef}
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {/* Top pane: Gantt chart */}
            <div
              style={{
                height: topHeight,
                flexShrink: 0,
                overflowY: "auto",
                padding: "10px 14px",
              }}
            >
              {loading && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "20px 0" }}>
                  {[80, 65, 75, 55, 70].map((w, i) => (
                    <div key={i} className="skeleton" style={{ height: 32, borderRadius: 6, width: `${w}%` }} />
                  ))}
                </div>
              )}
              {error && (
                <div style={{ padding: "16px", borderRadius: 8, background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.15)", color: "#EF4444", fontSize: 12, fontFamily: "var(--font-jb-mono, monospace)" }}>
                  {error}
                </div>
              )}
              {!loading && !error && nodes.length === 0 && (
                <div style={{ padding: "40px 0", textAlign: "center", color: "var(--text-dim)", fontSize: 13 }}>
                  No trace events yet
                </div>
              )}
              {!loading && !error && nodes.length > 0 && (
                <TimelineView
                  nodes={nodes}
                  selectedId={selectedNode?.id ?? null}
                  onSelect={setSelectedNode}
                />
              )}
            </div>

            {/* Drag handle */}
            <div
              onMouseDown={onDividerMouseDown}
              style={{
                height: 6,
                flexShrink: 0,
                background: "var(--bg-elevated)",
                borderTop: "1px solid var(--border-strong)",
                borderBottom: "1px solid var(--border-strong)",
                cursor: "row-resize",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                userSelect: "none",
              }}
            >
              <div
                style={{
                  width: 32,
                  height: 2,
                  borderRadius: 2,
                  background: "rgba(148,163,184,0.2)",
                }}
              />
            </div>

            {/* Bottom pane: detail panel */}
            <div style={{ flex: 1, overflow: "hidden", background: "var(--bg-base)" }}>
              <TimelineDetailPanel node={selectedNode} />
            </div>
          </div>
        ) : (
          // ── Normal scrollable body for trace / raw tabs ──────────────────────
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "10px 14px",
            }}
          >
            {loading && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  padding: "20px 0",
                }}
              >
                {[80, 65, 75, 55, 70].map((w, i) => (
                  <div
                    key={i}
                    className="skeleton"
                    style={{ height: 32, borderRadius: 6, width: `${w}%` }}
                  />
                ))}
              </div>
            )}

            {error && (
              <div
                style={{
                  padding: "16px",
                  borderRadius: 8,
                  background: "rgba(239,68,68,0.08)",
                  border: "1px solid rgba(239,68,68,0.15)",
                  color: "#EF4444",
                  fontSize: 12,
                  fontFamily: "var(--font-jb-mono, monospace)",
                }}
              >
                {error}
              </div>
            )}

            {!loading && !error && nodes.length === 0 && (
              <div
                style={{
                  padding: "40px 0",
                  textAlign: "center",
                  color: "var(--text-dim)",
                  fontSize: 13,
                }}
              >
                No trace events yet
              </div>
            )}

            {!loading && !error && nodes.length > 0 && (
              <>
                {view === "trace" && (
                  <div>
                    {nodes.map((node) => (
                      <TraceNodeRow
                        key={node.id}
                        node={node}
                        totalMs={calcTotalMs(nodes)}
                        onSelect={setSelectedNode}
                        isSelected={selectedNode?.id === node.id}
                      />
                    ))}
                  </div>
                )}

                {view === "raw" && (
                  <Highlight
                    theme={JSON_THEME}
                    code={JSON.stringify(events, null, 2)}
                    language="json"
                  >
                    {({ style, tokens, getLineProps, getTokenProps }) => (
                      <pre
                        style={{
                          ...style,
                          fontSize: 11,
                          fontFamily: "var(--font-jb-mono, monospace)",
                          lineHeight: 1.7,
                          overflowX: "auto",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          margin: 0,
                          padding: 0,
                        }}
                      >
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
                )}
              </>
            )}
          </div>
        )}

        {/* Footer: node count + legend */}
        <div
          style={{
            padding: "8px 18px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg-elevated)",
            display: "flex",
            alignItems: "center",
            gap: 16,
            flexShrink: 0,
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontFamily: "var(--font-jb-mono, monospace)",
              color: "var(--text-dim)",
            }}
          >
            {nodes.length} events
          </span>
          <div style={{ flex: 1 }} />
          {Object.entries(NODE_CONFIG).map(([type, cfg]) => (
            <div
              key={type}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                fontSize: 10.5,
                color: "var(--text-muted)",
              }}
            >
              <span style={{ color: cfg.color, fontSize: 9 }}>{cfg.icon}</span>
              {cfg.label}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// ─── Threads Table ───────────────────────────────────────────────────────────

function ThreadsTable({
  threads,
  onSelect,
  selectedId,
}: {
  threads: ThreadSummary[];
  onSelect: (t: ThreadSummary) => void;
  selectedId: string | null;
}) {
  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <table
        style={{
          width: "100%",
          borderCollapse: "collapse",
          tableLayout: "fixed",
        }}
      >
        <colgroup>
          <col style={{ width: 90 }} />
          <col style={{ width: 100 }} />
          <col style={{ width: 140 }} />
          <col style={{ width: 90 }} />
          <col style={{ width: 60 }} />
          <col style={{ width: 80 }} />
          <col style={{ width: 90 }} />
          <col style={{ width: 110 }} />
          <col style={{ width: 90 }} />
          <col style={{ width: 24 }} />
        </colgroup>
        <thead>
          <tr>
            {[
              "Status",
              "Thread",
              "Agent",
              "Model",
              "Steps",
              "Tokens",
              "Duration",
              "Started",
              "Trigger",
              "",
            ].map((h) => (
              <th
                key={h}
                style={{
                  textAlign: "left",
                  padding: "8px 12px",
                  fontSize: 10.5,
                  fontWeight: 600,
                  letterSpacing: "0.06em",
                  color: "var(--text-dim)",
                  borderBottom: "1px solid var(--border)",
                  userSelect: "none",
                  whiteSpace: "nowrap",
                  background: "var(--bg-base)",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {threads.map((t) => {
            const isSelected = selectedId === t.thread_id;
            return (
              <tr
                key={t.thread_id}
                onClick={() => onSelect(t)}
                style={{
                  cursor: "pointer",
                  background: isSelected
                    ? "rgba(59,130,246,0.06)"
                    : "transparent",
                  borderLeft: isSelected
                    ? "2px solid #3B82F6"
                    : "2px solid transparent",
                  transition: "background 0.1s",
                }}
                onMouseEnter={(e) => {
                  if (!isSelected)
                    (e.currentTarget as HTMLTableRowElement).style.background =
                      "rgba(148,163,184,0.03)";
                }}
                onMouseLeave={(e) => {
                  if (!isSelected)
                    (e.currentTarget as HTMLTableRowElement).style.background =
                      "transparent";
                }}
              >
                <td style={{ padding: "10px 12px" }}>
                  <StatusBadge status={t.status} />
                </td>
                <td style={{ padding: "10px 12px" }}>
                  <code
                    style={{
                      fontFamily: "var(--font-jb-mono, monospace)",
                      fontSize: 11.5,
                      color: "var(--text-secondary)",
                      background: "var(--border)",
                      padding: "2px 6px",
                      borderRadius: 3,
                    }}
                  >
                    {truncId(t.thread_id)}
                  </code>
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 12.5,
                    color: "var(--text-primary)",
                    fontWeight: 500,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t.agent_name || t.agent_id}
                  {t.agents && t.agents.length > 1 && (
                    <span
                      style={{
                        marginLeft: 5,
                        fontSize: 10,
                        color: "#8B5CF6",
                        background: "rgba(139,92,246,0.1)",
                        padding: "1px 5px",
                        borderRadius: 3,
                      }}
                    >
                      +{t.agents.length - 1}
                    </span>
                  )}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 11,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {shortModel(t.model)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 12,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                    textAlign: "center",
                  }}
                >
                  {t.step_index}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 11.5,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {formatTokens(t.tokens_used)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 11.5,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-secondary)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {formatDuration(t.duration_ms)}
                </td>
                <td
                  style={{
                    padding: "10px 12px",
                    fontSize: 11.5,
                    fontFamily: "var(--font-jb-mono, monospace)",
                    color: "var(--text-muted)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {formatRelTime(t.started_at)}
                </td>
                <td style={{ padding: "10px 12px" }}>
                  <TriggerChip trigger={t.trigger} />
                </td>
                <td style={{ padding: "10px 8px", textAlign: "right" }}>
                  <span
                    style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1 }}
                  >
                    →
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── Filter Bar ───────────────────────────────────────────────────────────────

const STATUS_FILTERS = [
  { key: "", label: "All" },
  { key: "running", label: "Running" },
  { key: "completed", label: "Completed" },
  { key: "error", label: "Failed" },
  { key: "pending", label: "Pending" },
];

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function ThreadsClient() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ThreadSummary | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchThreads = useCallback(async () => {
    try {
      const data = await listThreads(statusFilter || undefined);
      setThreads(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load threads");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  const checkStatus = useCallback(async () => {
    const s = await checkApiStatus();
    setApiStatus(s);
  }, []);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 15000);
    return () => clearInterval(interval);
  }, [checkStatus]);

  useEffect(() => {
    setLoading(true);
    fetchThreads();
  }, [fetchThreads]);

  useEffect(() => {
    if (!autoRefresh) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(fetchThreads, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [autoRefresh, fetchThreads]);

  // Filter threads by search
  const filteredThreads = threads.filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      t.thread_id.toLowerCase().includes(q) ||
      t.agent_name?.toLowerCase().includes(q) ||
      t.agent_id.toLowerCase().includes(q) ||
      t.model.toLowerCase().includes(q)
    );
  });

  // Count by status
  const counts = {
    "": threads.length,
    running: threads.filter((t) => normalizeStatus(t.status) === "running").length,
    completed: threads.filter((t) => normalizeStatus(t.status) === "completed").length,
    error: threads.filter((t) => normalizeStatus(t.status) === "error").length,
    pending: threads.filter((t) => normalizeStatus(t.status) === "pending").length,
  };

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-base)" }}>
      <TopNav
        apiStatus={apiStatus}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        onRefresh={fetchThreads}
      />

      {/* Page content */}
      <div style={{ paddingTop: 48 }}>
        {/* Page header */}
        <div
          style={{
            padding: "22px 24px 16px",
            borderBottom: "1px solid var(--border)",
          }}
        >
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
                Threads
              </h1>
              <p
                style={{
                  margin: "4px 0 0",
                  fontSize: 12.5,
                  color: "var(--text-muted)",
                }}
              >
                {threads.length} total runs · local mode
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
                placeholder="Search threads, agents…"
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

          {/* Status filter tabs */}
          <div style={{ display: "flex", gap: 4 }}>
            {STATUS_FILTERS.map((f) => {
              const isActive = statusFilter === f.key;
              const count = counts[f.key as keyof typeof counts] ?? 0;
              return (
                <button
                  key={f.key}
                  onClick={() => {
                    setStatusFilter(f.key);
                    setLoading(true);
                  }}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "4px 12px",
                    borderRadius: 5,
                    border: isActive
                      ? "1px solid rgba(59,130,246,0.3)"
                      : "1px solid var(--border-strong)",
                    background: isActive
                      ? "var(--accent-blue-dim)"
                      : "transparent",
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
                        background: isActive
                          ? "rgba(59,130,246,0.15)"
                          : "var(--border-strong)",
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
            <div
              style={{
                padding: "20px 24px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="skeleton"
                  style={{ height: 44, borderRadius: 6 }}
                />
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
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "#EF4444",
                  marginBottom: 6,
                }}
              >
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
                    fontFamily: "var(--font-jb-mono, monospace)",
                    background: "var(--border-strong)",
                    color: "var(--text-secondary)",
                    padding: "1px 6px",
                    borderRadius: 3,
                  }}
                >
                  ninetrix dev
                </code>
                {" "}to start the local stack
              </div>
            </div>
          )}

          {!loading && !error && filteredThreads.length === 0 && (
            <div
              style={{
                padding: "60px 24px",
                textAlign: "center",
              }}
            >
              <div
                style={{
                  fontSize: 32,
                  marginBottom: 12,
                  opacity: 0.2,
                  fontFamily: "var(--font-syne, sans-serif)",
                }}
              >
                ◎
              </div>
              <div style={{ fontSize: 14, color: "var(--text-muted)" }}>
                {search ? "No threads match your search" : "No threads yet"}
              </div>
              {!search && (
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 6 }}>
                  Run an agent with{" "}
                  <code
                    style={{
                      fontFamily: "var(--font-jb-mono, monospace)",
                      background: "var(--border-strong)",
                      padding: "1px 6px",
                      borderRadius: 3,
                      color: "var(--text-secondary)",
                    }}
                  >
                    agentfile run
                  </code>{" "}
                  to see traces here
                </div>
              )}
            </div>
          )}

          {!loading && !error && filteredThreads.length > 0 && (
            <ThreadsTable
              threads={filteredThreads}
              onSelect={setSelected}
              selectedId={selected?.thread_id ?? null}
            />
          )}
        </div>
      </div>

      {/* Trace drawer */}
      {selected && (
        <TraceDrawer
          thread={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
