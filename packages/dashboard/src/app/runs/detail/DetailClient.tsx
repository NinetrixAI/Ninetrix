"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Highlight, type PrismTheme } from "prism-react-renderer";
import {
  listThreads,
  listAgents,
  listChannels,
  getThreadTimeline,
  checkApiStatus,
  subscribeThreadStream,
  type ThreadSummary,
  type TimelineEvent,
  type AgentStats,
  type ApiStatus,
  type Channel,
  type StreamUpdate,
} from "@/lib/api";
import { timelineEventsToTraceNodes, calcTotalMs, type TraceNode } from "@/lib/trace";
import Sidebar from "@/components/sidebar";
import StatusBadge from "@/components/status-badge";
import { NodeIcon, WrenchIcon } from "@/components/node-icons";
import {
  formatDuration,
  formatTokens,
  formatTimestamp,
  formatCost,
  shortModel,
  normalizeStatus,
} from "@/lib/utils";

/* ── Theme ───────────────────────────────────────────────────────────────── */

const JSON_THEME: PrismTheme = {
  plain: { backgroundColor: "transparent", color: "#888892" },
  styles: [
    { types: ["property"], style: { color: "#60A5FA" } },
    { types: ["string"], style: { color: "#4ADE80" } },
    { types: ["number", "boolean", "null", "keyword"], style: { color: "#FBBF24" } },
    { types: ["punctuation", "operator"], style: { color: "#4B5563" } },
  ],
};

/* ── Node config ─────────────────────────────────────────────────────────── */

const NODE_CFG: Record<string, { color: string; dim: string; label: string }> = {
  llm:      { color: "#60A5FA", dim: "rgba(96,165,250,0.12)",  label: "LLM" },
  tool:     { color: "#4ADE80", dim: "rgba(74,222,128,0.12)",  label: "Tool" },
  thinking: { color: "#FBBF24", dim: "rgba(251,191,36,0.12)",  label: "Think" },
  handoff:  { color: "#A78BFA", dim: "rgba(167,139,250,0.12)", label: "Handoff" },
};

/* ── Gantt constants ─────────────────────────────────────────────────────── */

const LABEL_W = 200;
const ROW_H = 32;
const DURATION_COL_W = 64;
const DEFAULT_VIEW_MS = 6000;
const MIN_VIEW_MS = 300;

function getTickInterval(ms: number): number {
  if (ms < 200) return 50;
  if (ms < 1000) return 200;
  if (ms < 5000) return 1000;
  if (ms < 20000) return 2000;
  if (ms < 60000) return 10000;
  if (ms < 300000) return 30000;
  return 60000;
}

function formatTick(ms: number): string {
  if (ms === 0) return "0";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1)}s`;
  return `${Math.floor(ms / 60000)}m${Math.floor((ms % 60000) / 1000)}s`;
}

/* ── ContentBlock ────────────────────────────────────────────────────────── */

function ContentBlock({ title, content, accent }: { title: string; content: string; accent: string }) {
  const [expanded, setExpanded] = useState(true);
  if (!content) return null;

  const trimmed = content.trim();
  const isJson = trimmed.startsWith("{") || trimmed.startsWith("[");
  let formatted = content;
  if (isJson) {
    try { formatted = JSON.stringify(JSON.parse(trimmed), null, 2); } catch { /* keep raw */ }
  }

  return (
    <div
      style={{
        marginTop: 8,
        borderRadius: 6,
        border: `1px solid ${accent}22`,
        background: `${accent}08`,
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between cursor-pointer"
        style={{
          padding: "6px 10px",
          background: "transparent",
          border: "none",
          color: "var(--text-dim)",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textAlign: "left",
        }}
      >
        <span style={{ color: accent }}>{title}</span>
        <span style={{ fontSize: 9, opacity: 0.5 }}>{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>
      {expanded && (
        <div style={{ maxHeight: 320, overflowY: "auto" }}>
          {isJson ? (
            <Highlight theme={JSON_THEME} code={formatted} language="json">
              {({ tokens, getLineProps, getTokenProps }) => (
                <pre
                  style={{
                    margin: 0,
                    padding: "0 10px 10px",
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                    lineHeight: 1.7,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {tokens.map((line, i) => (
                    <div key={i} {...getLineProps({ line })}>
                      {line.map((token, j) => (
                        <span key={j} {...getTokenProps({ token })} />
                      ))}
                    </div>
                  ))}
                </pre>
              )}
            </Highlight>
          ) : (
            <pre
              style={{
                margin: 0,
                padding: "0 10px 10px",
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: "var(--text)",
                lineHeight: 1.7,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {content || "(empty)"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ── EventDetail ─────────────────────────────────────────────────────────── */

function EventDetail({ node }: { node: TraceNode | null }) {
  if (!node) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3" style={{ padding: 24 }}>
        <div style={{ fontSize: 28, opacity: 0.12, color: "var(--text-dim)" }}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4l3 3" />
          </svg>
        </div>
        <span style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-mono)", textAlign: "center" }}>
          Select an event to inspect
        </span>
      </div>
    );
  }

  const cfg = NODE_CFG[node.type] ?? NODE_CFG.tool;
  const isError = node.status === "error";

  return (
    <div className="animate-fade-in" style={{ padding: "14px 16px", overflowY: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 14, paddingBottom: 12, borderBottom: "1px solid var(--border)" }}>
        <div
          className="flex items-center justify-center shrink-0"
          style={{
            width: 32, height: 32, borderRadius: 7,
            background: isError ? "var(--red-dim)" : cfg.dim,
            color: isError ? "var(--red)" : cfg.color,
          }}
        >
          <NodeIcon type={node.type} size={16} color={isError ? "var(--red)" : cfg.color} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="flex items-center gap-2 flex-wrap">
            <span
              style={{
                fontSize: 13, fontFamily: "var(--font-mono)",
                color: isError ? "var(--red)" : "var(--text)",
                fontWeight: 600,
              }}
            >
              {node.label}
            </span>
            <span
              style={{
                fontSize: 10.5, color: cfg.color, background: cfg.dim,
                padding: "1px 7px", borderRadius: 4,
              }}
            >
              {cfg.label}
            </span>
            {isError && (
              <span style={{ fontSize: 10.5, color: "var(--red)", background: "var(--red-dim)", padding: "1px 7px", borderRadius: 4 }}>
                ERROR
              </span>
            )}
          </div>
          {/* Meta */}
          <div className="flex flex-wrap gap-3" style={{ marginTop: 6 }}>
            {[
              { l: "START", v: `+${formatDuration(node.startOffsetMs)}` },
              ...(node.durationMs != null ? [{ l: "DURATION", v: formatDuration(node.durationMs) }] : []),
              ...(node.model ? [{ l: "MODEL", v: shortModel(node.model) }] : []),
              ...(node.inputTokens != null ? [{ l: "IN", v: String(node.inputTokens) }] : []),
              ...(node.outputTokens != null ? [{ l: "OUT", v: String(node.outputTokens) }] : []),
              ...(node.estimatedCostUsd != null ? [{ l: "COST", v: `$${node.estimatedCostUsd.toFixed(5)}` }] : []),
            ].map((m) => (
              <div key={m.l}>
                <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", marginBottom: 1 }}>{m.l}</div>
                <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{m.v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Content blocks */}
      {node.type === "llm" && node.llmContent && (
        <>
          <ContentBlock title="PROMPT" content={node.llmContent.prompt} accent={cfg.color} />
          <ContentBlock title="RESPONSE" content={node.llmContent.response} accent={cfg.color} />
        </>
      )}
      {node.type === "tool" && node.toolContent && (
        <>
          <ContentBlock title="INPUT" content={node.toolContent.input} accent={cfg.color} />
          <ContentBlock title="OUTPUT" content={node.toolContent.output || "(pending...)"} accent={isError ? "var(--red)" : cfg.color} />
        </>
      )}
      {node.type === "thinking" && node.thinkingContent && (
        <ContentBlock title="REASONING" content={node.thinkingContent.text} accent={cfg.color} />
      )}
      {node.type === "handoff" && node.handoffContent && (
        <>
          <ContentBlock title="TARGET AGENT" content={node.handoffContent.targetAgent} accent={cfg.color} />
          <ContentBlock title="MESSAGE" content={node.handoffContent.message} accent={cfg.color} />
        </>
      )}

      {/* Timestamp */}
      <div
        style={{
          marginTop: 12, padding: "8px 10px", borderRadius: 5,
          background: "var(--bg-raised)", border: "1px solid var(--border)",
        }}
      >
        <span style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: "0.07em", color: "var(--text-dim)", marginRight: 8 }}>
          TIMESTAMP
        </span>
        <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
          {formatTimestamp(node.absoluteStartIso)}
        </span>
      </div>
    </div>
  );
}

/* ── ToolsPanel ──────────────────────────────────────────────────────────── */

function ToolsPanel({ nodes }: { nodes: TraceNode[] }) {
  const toolStats = useMemo(() => {
    const map = new Map<string, { calls: number; errors: number; totalMs: number }>();
    for (const n of nodes) {
      if (n.type === "tool") {
        const e = map.get(n.label) ?? { calls: 0, errors: 0, totalMs: 0 };
        map.set(n.label, {
          calls: e.calls + 1,
          errors: e.errors + (n.status === "error" ? 1 : 0),
          totalMs: e.totalMs + (n.durationMs ?? 0),
        });
      }
    }
    return Array.from(map.entries())
      .map(([name, s]) => ({ name, ...s, avgMs: s.calls > 0 ? s.totalMs / s.calls : 0 }))
      .sort((a, b) => b.calls - a.calls);
  }, [nodes]);

  if (toolStats.length === 0) return null;

  return (
    <div>
      <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: "0.08em", color: "var(--text-dim)", marginBottom: 10 }}>
        TOOLS
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))", gap: 8 }}>
        {toolStats.map((t) => {
          const hasErr = t.errors > 0;
          const rate = t.calls > 0 ? ((t.calls - t.errors) / t.calls) * 100 : 100;
          return (
            <div
              key={t.name}
              style={{
                padding: "10px 12px", borderRadius: 8,
                background: "var(--bg-surface)", border: `1px solid ${hasErr ? "rgba(239,68,68,0.15)" : "var(--border)"}`,
              }}
            >
              <div className="flex items-center gap-2" style={{ marginBottom: 6 }}>
                <WrenchIcon size={12} color={hasErr ? "var(--red)" : "var(--green)"} />
                <span
                  className="truncate"
                  style={{ fontSize: 11.5, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}
                >
                  {t.name}
                </span>
              </div>
              <div className="flex gap-3" style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>
                <div>
                  <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>CALLS</div>
                  <div style={{ color: "var(--text)", fontWeight: 600 }}>{t.calls}</div>
                </div>
                <div>
                  <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>AVG</div>
                  <div style={{ color: "var(--text-secondary)" }}>{formatDuration(t.avgMs)}</div>
                </div>
                {t.errors > 0 && (
                  <div>
                    <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", marginBottom: 1 }}>ERR</div>
                    <div style={{ color: "var(--red)", fontWeight: 600 }}>{t.errors}</div>
                  </div>
                )}
              </div>
              <div style={{ height: 3, borderRadius: 2, background: "var(--bg-raised)", overflow: "hidden", marginTop: 6 }}>
                <div style={{ height: "100%", width: `${rate}%`, background: hasErr ? "linear-gradient(90deg, var(--green), var(--red))" : "var(--green)", borderRadius: 2 }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── Waterfall chart ─────────────────────────────────────────────────────── */

function WaterfallChart({
  nodes,
  totalMs,
  liveMs,
  isRunning,
  selectedId,
  onSelect,
}: {
  nodes: TraceNode[];
  totalMs: number;
  liveMs: number;
  isRunning: boolean;
  selectedId: string | null;
  onSelect: (n: TraceNode) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(800);
  const [viewWindowMs, setViewWindowMs] = useState(DEFAULT_VIEW_MS);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const initRef = useRef(false);

  const effectiveTotal = Math.max(liveMs, totalMs, 1);

  // Keyboard navigation
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      if (!selectedId || nodes.length === 0) return;
      const idx = nodes.findIndex((n) => n.id === selectedId);
      if (idx < 0) return;
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(idx + 1, nodes.length - 1) : Math.max(idx - 1, 0);
      if (next !== idx) onSelect(nodes[next]);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [selectedId, nodes, onSelect]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!initRef.current && effectiveTotal > 100) {
      initRef.current = true;
      setViewWindowMs(effectiveTotal);
    }
  }, [effectiveTotal]);

  const trackWidth = Math.max(containerWidth - LABEL_W - DURATION_COL_W, 100);
  const pxPerMs = trackWidth / viewWindowMs;
  const chartAreaPx = Math.max(trackWidth, Math.round(pxPerMs * effectiveTotal));

  const tickInterval = getTickInterval(viewWindowMs);
  const ticks: number[] = [];
  for (let t = 0; t <= effectiveTotal + tickInterval; t += tickInterval) {
    if (t > effectiveTotal * 1.05) break;
    ticks.push(Math.min(t, effectiveTotal));
  }
  if (ticks[ticks.length - 1] < effectiveTotal) ticks.push(effectiveTotal);
  const uniqueTicks = Array.from(new Set(ticks));

  const zoomIn = () => setViewWindowMs((v) => Math.max(MIN_VIEW_MS, v / 1.6));
  const zoomOut = () => setViewWindowMs((v) => Math.min(effectiveTotal, v * 1.6));
  const zoomFit = () => { setViewWindowMs(effectiveTotal); scrollRef.current && (scrollRef.current.scrollLeft = 0); };

  const zoomLabel = viewWindowMs < 1000 ? `${Math.round(viewWindowMs)}ms` : `${(viewWindowMs / 1000).toFixed(1)}s`;

  // Group nodes by agent for multi-agent headers
  const isMultiAgent = useMemo(() => {
    const agents = new Set(nodes.map((n) => n.agentId));
    return agents.size > 1;
  }, [nodes]);

  const stickyBg = "var(--bg-surface)";

  return (
    <div style={{ background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)", overflow: "hidden" }}>
      {/* Zoom toolbar */}
      <div className="flex items-center gap-2" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: "0.08em", color: "var(--text-dim)", fontFamily: "var(--font-mono)", marginRight: 4 }}>
          WATERFALL
        </span>
        {[
          { label: "+", fn: zoomIn, title: "Zoom in" },
          { label: "\u2212", fn: zoomOut, title: "Zoom out" },
          { label: "Fit", fn: zoomFit, title: "Fit trace" },
        ].map((btn) => (
          <button
            key={btn.label}
            onClick={btn.fn}
            title={btn.title}
            className="inline-flex items-center justify-center cursor-pointer"
            style={{
              height: 24, minWidth: 28, padding: "0 6px",
              borderRadius: 4, border: "1px solid var(--border)",
              background: "var(--bg-raised)", color: "var(--text-secondary)",
              fontSize: 12, fontWeight: 600, fontFamily: "var(--font-mono)",
            }}
          >
            {btn.label}
          </button>
        ))}
        <span
          style={{
            fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-dim)",
            padding: "2px 7px", borderRadius: 4, background: "var(--bg-raised)",
            border: "1px solid var(--border)",
          }}
        >
          {zoomLabel}/view
        </span>
        {isRunning && (
          <span className="flex items-center gap-1.5 ml-2" style={{ fontSize: 10, fontWeight: 600, color: "var(--green)" }}>
            <span className="rounded-full" style={{ width: 5, height: 5, background: "var(--green)", animation: "pulse-dot 2s ease-in-out infinite" }} />
            LIVE
          </span>
        )}

        {/* Legend + event count (right-aligned) */}
        <div className="flex items-center gap-3 ml-auto">
          {Object.entries(NODE_CFG).map(([key, cfg]) => (
            <div key={key} className="flex items-center gap-1.5" style={{ fontSize: 10.5, color: "var(--text-dim)" }}>
              <NodeIcon type={key} size={12} color={cfg.color} />
              {cfg.label}
            </div>
          ))}
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
            {nodes.length} events
          </span>
        </div>
      </div>

      {/* Chart */}
      <div ref={scrollRef} style={{ width: "100%", overflowX: "auto", overflowY: "visible" }}>
        <div style={{ width: LABEL_W + chartAreaPx + DURATION_COL_W, minWidth: "100%" }}>
          {/* Ruler */}
          <div className="flex items-end" style={{ height: 28, paddingBottom: 6, borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: stickyBg, zIndex: 10 }}>
            <div style={{ width: LABEL_W, flexShrink: 0, position: "sticky", left: 0, zIndex: 12, background: stickyBg }} />
            <div style={{ width: chartAreaPx, flexShrink: 0, position: "relative" }}>
              {uniqueTicks.map((t) => (
                <span
                  key={t}
                  style={{
                    position: "absolute",
                    left: Math.round(t * pxPerMs),
                    transform: t === effectiveTotal ? "translateX(-100%)" : t === 0 ? "none" : "translateX(-50%)",
                    fontSize: 9.5, fontFamily: "var(--font-mono)", color: "var(--text-dim)",
                    whiteSpace: "nowrap", lineHeight: 1,
                  }}
                >
                  {formatTick(t)}
                </span>
              ))}
            </div>
            <div style={{ width: DURATION_COL_W, flexShrink: 0, position: "sticky", right: 0, zIndex: 12, background: stickyBg }} />
          </div>

          {/* Grid lines + rows */}
          <div style={{ position: "relative" }}>
            {/* Grid lines */}
            {uniqueTicks.slice(1).map((t) => (
              <div key={t} style={{
                position: "absolute", left: LABEL_W + Math.round(t * pxPerMs),
                top: 0, bottom: 0, width: 1, background: "var(--bg-hover)",
                zIndex: 0, pointerEvents: "none",
              }} />
            ))}

            {/* Live cursor */}
            {isRunning && (
              <div style={{
                position: "absolute", left: LABEL_W + chartAreaPx,
                top: 0, bottom: 0, width: 2,
                background: "rgba(74,222,128,0.6)", zIndex: 5, pointerEvents: "none",
                boxShadow: "0 0 8px rgba(74,222,128,0.3)",
              }} />
            )}

            {/* Rows */}
            {nodes.length === 0 && (
              <div className="flex items-center justify-center" style={{ height: 100, color: "var(--text-dim)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
                {isRunning ? "Agent is starting..." : "No events yet"}
              </div>
            )}

            {nodes.map((node, idx) => {
              const cfg = NODE_CFG[node.type] ?? NODE_CFG.tool;
              const leftPx = Math.round(node.startOffsetMs * pxPerMs);
              const widthPx = node.durationMs
                ? Math.max(4, Math.round(node.durationMs * pxPerMs))
                : node.status === "running"
                ? Math.max(8, Math.round((liveMs - node.startOffsetMs) * pxPerMs))
                : 4;
              const isHovered = hoveredId === node.id;
              const isSelected = selectedId === node.id;
              const isError = node.status === "error";
              const isNodeRunning = node.status === "running";

              const prevNode = nodes[idx - 1];
              const showAgentHeader = isMultiAgent && node.agentId !== prevNode?.agentId;

              const rowBg = isSelected ? cfg.dim : isHovered ? "var(--bg-hover)" : stickyBg;

              return (
                <div key={node.id}>
                  {showAgentHeader && (
                    <div className="flex items-center" style={{ height: 22, borderTop: idx > 0 ? "1px solid var(--border)" : "none", marginTop: idx > 0 ? 4 : 0 }}>
                      <div style={{ width: LABEL_W, flexShrink: 0, position: "sticky", left: 0, zIndex: 4, background: stickyBg, paddingLeft: 10, height: "100%", display: "flex", alignItems: "center" }}>
                        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
                          AGENT: {node.agentId}
                        </span>
                      </div>
                    </div>
                  )}

                  <div
                    onMouseEnter={() => setHoveredId(node.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    onClick={() => onSelect(node)}
                    className="flex items-stretch cursor-pointer transition-colors"
                    style={{
                      height: ROW_H,
                      background: isSelected ? cfg.dim : isHovered ? "var(--bg-hover)" : "transparent",
                    }}
                  >
                    {/* Label */}
                    <div
                      className="flex items-center gap-1.5 shrink-0"
                      style={{
                        width: LABEL_W, paddingLeft: 10 + node.depth * 14, paddingRight: 8,
                        position: "sticky", left: 0, zIndex: 3, background: rowBg,
                        boxShadow: isSelected ? `inset 2px 0 0 ${cfg.color}` : "none",
                      }}
                    >
                      <span className="shrink-0 flex items-center" style={{
                        color: isError ? "var(--red)" : cfg.color,
                        opacity: isNodeRunning ? 1 : 0.85,
                        animation: isNodeRunning ? "pulse-dot 2s ease-in-out infinite" : "none",
                      }}>
                        <NodeIcon type={node.type} size={14} color={isError ? "var(--red)" : cfg.color} />
                      </span>
                      <span
                        className="truncate"
                        style={{
                          fontSize: 11.5, fontFamily: "var(--font-mono)",
                          color: isError ? "var(--red)" : isSelected ? "var(--text)" : "var(--text-secondary)",
                          fontWeight: isSelected ? 500 : 400,
                        }}
                      >
                        {node.label}
                      </span>
                    </div>

                    {/* Bar */}
                    <div style={{ width: chartAreaPx, flexShrink: 0, height: ROW_H, position: "relative" }}>
                      <div
                        style={{
                          position: "absolute", left: leftPx, width: widthPx,
                          top: 8, bottom: 8, borderRadius: 3,
                          background: isError ? "rgba(239,68,68,0.7)" : isHovered ? cfg.color : `${cfg.color}BB`,
                          transition: "left 0.2s ease, width 0.2s ease",
                          minWidth: 4,
                          boxShadow: isNodeRunning ? `0 0 6px ${cfg.color}66` : "none",
                        }}
                      />
                      {isNodeRunning && (
                        <div
                          style={{
                            position: "absolute", left: leftPx + widthPx, width: 3,
                            top: 8, bottom: 8, background: cfg.color,
                            borderRadius: "0 3px 3px 0",
                            animation: "pulse-dot 1s ease-in-out infinite",
                            transition: "left 0.2s ease",
                          }}
                        />
                      )}
                    </div>

                    {/* Duration */}
                    <div
                      className="flex items-center justify-end shrink-0"
                      style={{
                        width: DURATION_COL_W, paddingRight: 12,
                        fontSize: 10.5, fontFamily: "var(--font-mono)",
                        color: isError ? "var(--red)" : "var(--text-dim)",
                        position: "sticky", right: 0, zIndex: 3, background: rowBg,
                      }}
                    >
                      {isNodeRunning ? "..." : formatDuration(node.durationMs)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

    </div>
  );
}

/* ── Timeline (Event Feed) ───────────────────────────────────────────────── */

type EventFilter = "all" | "llm" | "tool" | "thinking" | "handoff" | "error";

function Timeline({
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

  // Keyboard navigation
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      if (!selectedId || filtered.length === 0) return;
      const idx = filtered.findIndex((n) => n.id === selectedId);
      if (idx < 0) return;
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(idx + 1, filtered.length - 1) : Math.max(idx - 1, 0);
      if (next !== idx) onSelect(filtered[next]);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [selectedId, filtered, onSelect]);

  const errorCount = nodes.filter((n) => n.status === "error").length;

  const allFilters: { key: EventFilter; label: string; count: number }[] = [
    { key: "all", label: "All", count: nodes.length },
    { key: "llm", label: "LLM", count: nodes.filter((n) => n.type === "llm").length },
    { key: "tool", label: "Tools", count: nodes.filter((n) => n.type === "tool").length },
    { key: "thinking", label: "Think", count: nodes.filter((n) => n.type === "thinking").length },
    { key: "handoff", label: "Handoff", count: nodes.filter((n) => n.type === "handoff").length },
    ...(errorCount > 0 ? [{ key: "error" as EventFilter, label: "Errors", count: errorCount }] : []),
  ];
  const filters = allFilters.filter((f) => f.count > 0 || f.key === "all");

  return (
    <div style={{ background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)", overflow: "hidden" }}>
      {/* Filter bar */}
      <div className="flex gap-1 overflow-x-auto" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: "0.08em", color: "var(--text-dim)", fontFamily: "var(--font-mono)", marginRight: 6, display: "flex", alignItems: "center" }}>
          TIMELINE
        </span>
        {filters.map((f) => {
          const active = filter === f.key;
          const isErr = f.key === "error";
          return (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className="inline-flex items-center gap-1 cursor-pointer whitespace-nowrap"
              style={{
                padding: "3px 9px", borderRadius: 5, border: "none",
                fontSize: 11, fontWeight: 500,
                background: active ? (isErr ? "var(--red-dim)" : "var(--bg-raised)") : "transparent",
                color: active ? (isErr ? "var(--red)" : "var(--text)") : "var(--text-muted)",
              }}
            >
              {f.label}
              <span style={{ fontSize: 9.5, fontFamily: "var(--font-mono)", color: active ? "var(--text-secondary)" : "var(--text-dim)" }}>
                {f.count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Events */}
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {filtered.map((node) => {
          const cfg = NODE_CFG[node.type] ?? NODE_CFG.tool;
          const isSelected = selectedId === node.id;
          const isError = node.status === "error";
          const isNodeRunning = node.status === "running";
          const tokens = (node.inputTokens ?? 0) + (node.outputTokens ?? 0);

          // Preview text
          let preview = "";
          if (node.type === "llm" && node.llmContent?.response)
            preview = node.llmContent.response.slice(0, 80);
          else if (node.type === "tool" && node.toolContent?.input)
            preview = node.toolContent.input.slice(0, 80);
          else if (node.type === "thinking" && node.thinkingContent?.text)
            preview = node.thinkingContent.text.slice(0, 80);
          else if (node.type === "handoff" && node.handoffContent)
            preview = `-> ${node.handoffContent.targetAgent}`;

          return (
            <div
              key={node.id}
              onClick={() => onSelect(node)}
              className="flex items-start gap-2.5 cursor-pointer transition-colors"
              style={{
                padding: "8px 12px",
                background: isSelected ? cfg.dim : "transparent",
                boxShadow: isSelected ? `inset 2px 0 0 ${cfg.color}` : "none",
                borderBottom: "1px solid var(--border)",
              }}
              onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
              onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
            >
              {/* Icon */}
              <div
                className="flex items-center justify-center shrink-0"
                style={{
                  width: 24, height: 24, borderRadius: 5,
                  background: isError ? "var(--red-dim)" : cfg.dim,
                  color: isError ? "var(--red)" : cfg.color,
                  marginTop: 1,
                }}
              >
                <NodeIcon type={node.type} size={13} color={isError ? "var(--red)" : cfg.color} />
              </div>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="flex items-center gap-1.5" style={{ marginBottom: 2 }}>
                  <span className="truncate" style={{ fontSize: 12, fontWeight: 500, color: isError ? "var(--red)" : "var(--text)" }}>
                    {node.label}
                  </span>
                  <span style={{ fontSize: 10, color: cfg.color, background: cfg.dim, padding: "0 5px", borderRadius: 3, flexShrink: 0 }}>
                    {cfg.label.toLowerCase()}
                  </span>
                  {isNodeRunning && (
                    <span className="rounded-full shrink-0" style={{ width: 5, height: 5, background: "var(--green)", animation: "pulse-dot 2s ease-in-out infinite" }} />
                  )}
                </div>
                <div className="flex items-center gap-2" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                  <span>{formatTimestamp(node.absoluteStartIso)}</span>
                  {node.durationMs != null && <span>{formatDuration(node.durationMs)}</span>}
                  {tokens > 0 && <span>{formatTokens(tokens)}t</span>}
                </div>
                {preview && (
                  <div className="truncate" style={{ marginTop: 2, fontSize: 10.5, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {preview}{preview.length >= 80 ? "..." : ""}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div className="flex items-center justify-center" style={{ height: 80, color: "var(--text-dim)", fontSize: 12 }}>
            No events
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Budget helpers ──────────────────────────────────────────────────────── */

function budgetLevel(spent: number, budget: number, softWarned: boolean) {
  if (budget <= 0) return { level: "none", pct: 0 };
  const pct = Math.min((spent / budget) * 100, 100);
  if (spent >= budget) return { level: "exceeded", pct: 100 };
  if (pct >= 80) return { level: "critical", pct };
  if (softWarned || pct >= 50) return { level: "warning", pct };
  return { level: "normal", pct };
}

const BUDGET_COLORS: Record<string, { bar: string; text: string }> = {
  none:     { bar: "var(--purple)", text: "var(--text-secondary)" },
  normal:   { bar: "var(--purple)", text: "var(--purple)" },
  warning:  { bar: "var(--amber)",  text: "var(--amber)" },
  critical: { bar: "var(--orange)", text: "var(--orange)" },
  exceeded: { bar: "var(--red)",    text: "var(--red)" },
};

/* ── StatsLines ─────────────────────────────────────────────────────────── */

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: 14, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)", whiteSpace: "nowrap" }}>
        {value}
      </div>
    </div>
  );
}

function AgentsStat({ nodes, agentList }: { nodes: TraceNode[]; agentList: string[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const agentStats = useMemo(() => {
    const map = new Map<string, { model: string | null; llmCalls: number; toolCalls: number; tokens: number; cost: number }>();
    for (const agent of agentList) {
      map.set(agent, { model: null, llmCalls: 0, toolCalls: 0, tokens: 0, cost: 0 });
    }
    for (const n of nodes) {
      const e = map.get(n.agentId);
      if (!e) continue;
      if (n.type === "llm") {
        e.llmCalls++;
        if (n.model && !e.model) e.model = n.model;
      } else if (n.type === "tool") {
        e.toolCalls++;
      }
      e.tokens += (n.inputTokens ?? 0) + (n.outputTokens ?? 0);
      e.cost += n.estimatedCostUsd ?? 0;
    }
    return agentList.map((name) => ({ name, ...map.get(name)! }));
  }, [nodes, agentList]);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  if (agentList.length === 0) return null;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 3 }}>
        {agentList.length === 1 ? "Agent" : "Agents"}
      </div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="cursor-pointer inline-flex items-center gap-1.5"
        style={{
          fontSize: 14, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)",
          background: "none", border: "none", padding: 0,
          borderBottom: "1px dashed var(--text-dim)",
        }}
      >
        {agentList[0]}
        {agentList.length > 1 && (
          <span style={{ fontSize: 10, color: "var(--text-dim)" }}>
            +{agentList.length - 1}
          </span>
        )}
      </button>

      {open && (
        <div
          className="animate-fade-in"
          style={{
            position: "absolute", top: "100%", left: 0, marginTop: 8, zIndex: 50,
            background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)", width: 300, maxHeight: 320, overflowY: "auto",
          }}
        >
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)" }}>
              AGENTS
            </span>
          </div>
          {agentStats.map((a) => (
            <div key={a.name} style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2" style={{ marginBottom: 4 }}>
                <span className="truncate" style={{ fontSize: 12, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}>
                  {a.name}
                </span>
                {a.model && (
                  <span style={{
                    fontSize: 10, color: "var(--text-dim)", background: "var(--bg-raised)",
                    padding: "1px 6px", borderRadius: 3, border: "1px solid var(--border)", flexShrink: 0,
                  }}>
                    {shortModel(a.model)}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-4" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                <span>{a.llmCalls} llm</span>
                <span>{a.toolCalls} tool{a.toolCalls !== 1 ? "s" : ""}</span>
                <span>{formatTokens(a.tokens)}t</span>
                {a.cost > 0 && <span>{formatCost(a.cost)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ToolsStat({ nodes }: { nodes: TraceNode[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const toolStats = useMemo(() => {
    const map = new Map<string, { calls: number; errors: number; totalMs: number }>();
    for (const n of nodes) {
      if (n.type === "tool") {
        const e = map.get(n.label) ?? { calls: 0, errors: 0, totalMs: 0 };
        map.set(n.label, {
          calls: e.calls + 1,
          errors: e.errors + (n.status === "error" ? 1 : 0),
          totalMs: e.totalMs + (n.durationMs ?? 0),
        });
      }
    }
    return Array.from(map.entries())
      .map(([name, s]) => ({ name, ...s, avgMs: s.calls > 0 ? s.totalMs / s.calls : 0 }))
      .sort((a, b) => b.calls - a.calls);
  }, [nodes]);

  const totalToolCalls = toolStats.reduce((s, t) => s + t.calls, 0);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 3 }}>
        Tools
      </div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="cursor-pointer inline-flex items-center gap-1.5"
        style={{
          fontSize: 14, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)",
          background: "none", border: "none", padding: 0,
          borderBottom: toolStats.length > 0 ? "1px dashed var(--text-dim)" : "none",
        }}
      >
        {totalToolCalls}
        {toolStats.length > 0 && (
          <span style={{ fontSize: 10, color: "var(--text-dim)" }}>
            ({toolStats.length})
          </span>
        )}
      </button>

      {open && toolStats.length > 0 && (
        <div
          className="animate-fade-in"
          style={{
            position: "absolute", top: "100%", left: 0, marginTop: 8, zIndex: 50,
            background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)", width: 300, maxHeight: 320, overflowY: "auto",
          }}
        >
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)" }}>
              TOOLS USED
            </span>
          </div>
          {toolStats.map((t) => {
            const hasErr = t.errors > 0;
            const rate = t.calls > 0 ? ((t.calls - t.errors) / t.calls) * 100 : 100;
            return (
              <div
                key={t.name}
                style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}
              >
                <div className="flex items-center gap-2" style={{ marginBottom: 4 }}>
                  <WrenchIcon size={12} color={hasErr ? "var(--red)" : "var(--green)"} />
                  <span className="truncate" style={{ fontSize: 11.5, fontFamily: "var(--font-mono)", fontWeight: 500, color: "var(--text)" }}>
                    {t.name}
                  </span>
                </div>
                <div className="flex items-center gap-4" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                  <span>{t.calls} call{t.calls !== 1 ? "s" : ""}</span>
                  <span>avg {formatDuration(t.avgMs)}</span>
                  {t.errors > 0 && <span style={{ color: "var(--red)" }}>{t.errors} err</span>}
                </div>
                <div style={{ height: 3, borderRadius: 2, background: "var(--bg-raised)", overflow: "hidden", marginTop: 4 }}>
                  <div style={{ height: "100%", width: `${rate}%`, background: hasErr ? "linear-gradient(90deg, var(--green), var(--red))" : "var(--green)", borderRadius: 2 }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StatsLines({
  thread,
  stats,
  nodes,
  agentList,
  isRunning,
  liveMs,
}: {
  thread: ThreadSummary | null;
  stats: { totalTok: number; totalCost: number; llmCalls: number; toolCalls: number; errors: number };
  nodes: TraceNode[];
  agentList: string[];
  isRunning: boolean;
  liveMs: number;
}) {
  const hasBudget = thread && (thread.budget_usd ?? 0) > 0;
  const spent = thread?.run_cost_usd ?? 0;
  const budget = thread?.budget_usd ?? 0;
  const softWarned = thread?.budget_soft_warned ?? false;
  const { level, pct } = budgetLevel(spent, budget, softWarned);
  const bc = BUDGET_COLORS[level];

  return (
    <div style={{ display: "flex", gap: 40, alignItems: "start", flexWrap: "wrap" }}>
      <AgentsStat nodes={nodes} agentList={agentList} />
      <StatCell label="Duration" value={thread?.duration_ms != null ? formatDuration(thread.duration_ms) : isRunning ? formatDuration(liveMs) : "--"} />
      <StatCell label="Tokens" value={formatTokens(stats.totalTok)} />
      <StatCell label="LLM" value={String(stats.llmCalls)} />
      <ToolsStat nodes={nodes} />
      <StatCell label="Model" value={thread?.model ? shortModel(thread.model) : "--"} />
      <StatCell label="Cost" value={stats.totalCost > 0 ? formatCost(stats.totalCost) : "--"} />
      {hasBudget && (
        <StatCell label="Budget" value={`${formatCost(spent)} / ${formatCost(budget)}`} />
      )}
      {hasBudget && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 5 }}>
            Used
          </div>
          <div className="flex items-center gap-3">
            <div style={{ flex: 1, maxWidth: 100, height: 6, borderRadius: 3, background: "var(--bg-raised)", overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${pct}%`, borderRadius: 3, background: level === "exceeded" ? "linear-gradient(90deg, var(--orange), var(--red))" : bc.bar, transition: "width 0.3s ease" }} />
            </div>
            <span style={{ fontSize: 14, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
              {pct.toFixed(pct >= 10 ? 0 : 1)}%
            </span>
          </div>
        </div>
      )}
      {stats.errors > 0 && (
        <StatCell label="Errors" value={String(stats.errors)} />
      )}
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────────────────── */

export default function RunDetailPage() {
  const params = useSearchParams();
  const threadId = params.get("thread_id") ?? "";

  const [thread, setThread] = useState<ThreadSummary | null>(null);
  const [nodes, setNodes] = useState<TraceNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selectedNode, setSelectedNode] = useState<TraceNode | null>(null);
  const [liveMs, setLiveMs] = useState(0);
  const [tab, setTab] = useState<"waterfall" | "timeline">("waterfall");

  const sseRef = useRef<(() => void) | null>(null);
  const accEventsRef = useRef<TimelineEvent[]>([]);
  const sseFirstBatch = useRef(true);

  const isRunning = thread ? normalizeStatus(thread.status) === "running" : false;
  const isLive = thread ? (normalizeStatus(thread.status) === "running" || normalizeStatus(thread.status) === "idle") : false;

  const totalMs = useMemo(() => calcTotalMs(nodes), [nodes]);

  const fetchTimeline = useCallback(async (t: ThreadSummary) => {
    try {
      const evs = await getThreadTimeline(t.thread_id);
      accEventsRef.current = evs;
      setNodes(timelineEventsToTraceNodes(evs, t));
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load timeline");
    }
  }, []);

  // Initial load
  useEffect(() => {
    if (!threadId) { setLoading(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const { items } = await listThreads({ limit: 200 });
        const found = items.find((t) => t.thread_id === threadId);
        if (!found) { setError(`Run "${threadId}" not found`); setLoading(false); return; }
        if (cancelled) return;
        setThread(found);
        await fetchTimeline(found);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [threadId, fetchTimeline]);

  // Load sidebar data
  const fetchChannels = useCallback(() => {
    listChannels().then(setChannels).catch(() => {});
  }, []);
  useEffect(() => {
    listAgents({ limit: 100 }).then((d) => setAgents(d.items ?? [])).catch(() => {});
    checkApiStatus().then(setApiStatus);
    fetchChannels();
  }, [fetchChannels]);

  // SSE streaming
  useEffect(() => {
    if (!thread || !isLive) return;
    sseFirstBatch.current = true;

    const cleanup = subscribeThreadStream(
      thread.thread_id,
      (update: StreamUpdate) => {
        if (update.events.length > 0) {
          if (sseFirstBatch.current) {
            accEventsRef.current = update.events;
            sseFirstBatch.current = false;
          } else {
            accEventsRef.current = [...accEventsRef.current, ...update.events];
          }
          setNodes(timelineEventsToTraceNodes(accEventsRef.current, thread));
        }
        setThread((prev) => prev ? { ...prev, status: update.status, step_index: update.step_index } : prev);
      },
      () => {
        listThreads({ limit: 200 }).then(({ items }) => {
          const final = items.find((t) => t.thread_id === thread.thread_id);
          if (final) { setThread(final); fetchTimeline(final); }
        }).catch(() => {});
      },
    );
    sseRef.current = cleanup;
    return () => { cleanup(); sseRef.current = null; };
  }, [thread?.thread_id, isLive, fetchTimeline]);

  // Live timer
  useEffect(() => {
    if (!isRunning || !thread) return;
    const iv = setInterval(() => {
      const elapsed = Date.now() - new Date(thread.started_at).getTime();
      setLiveMs(elapsed);
    }, 200);
    return () => clearInterval(iv);
  }, [isRunning, thread?.started_at]);

  // Fallback liveMs from totalMs
  useEffect(() => {
    if (!isRunning) setLiveMs(totalMs);
  }, [isRunning, totalMs]);

  // Stats
  const stats = useMemo(() => {
    const totalTok = nodes.reduce((s, n) => s + (n.inputTokens ?? 0) + (n.outputTokens ?? 0), 0);
    const totalCost = nodes.reduce((s, n) => s + (n.estimatedCostUsd ?? 0), 0);
    const llmCalls = nodes.filter((n) => n.type === "llm").length;
    const toolCalls = nodes.filter((n) => n.type === "tool").length;
    const errors = nodes.filter((n) => n.status === "error").length;
    return { totalTok, totalCost, llmCalls, toolCalls, errors };
  }, [nodes]);

  // Multi-agent
  const agentList = useMemo(() => {
    const seen = new Set<string>();
    return nodes.reduce<string[]>((acc, n) => {
      if (!seen.has(n.agentId)) { seen.add(n.agentId); acc.push(n.agentId); }
      return acc;
    }, []);
  }, [nodes]);

  const AGENT_COLORS = ["#60A5FA", "#A78BFA", "#4ADE80", "#FBBF24", "#22D3EE", "#EF4444"];

  if (!threadId) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
        <div style={{ color: "var(--text-muted)", fontSize: 14 }}>
          No thread_id provided.{" "}
          <Link href="/runs" style={{ color: "var(--purple)" }}>Back to runs</Link>
        </div>
      </div>
    );
  }

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
        <header className="animate-fade-in" style={{ padding: "20px 32px", borderBottom: "1px solid var(--border)", position: "relative", zIndex: 30 }}>
          {/* Breadcrumb + agents — single row */}
          <div className="flex items-center gap-2" style={{ marginBottom: 12 }}>
            <Link
              href="/runs"
              className="inline-flex items-center justify-center no-underline transition-colors shrink-0"
              title="Back to runs"
              style={{
                width: 26, height: 26, borderRadius: 6,
                border: "1px solid var(--border)", color: "var(--text-muted)",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-raised)"; e.currentTarget.style.color = "var(--text)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M19 12H5M12 19l-7-7 7-7" />
              </svg>
            </Link>
            <span style={{ color: "var(--text-dim)", fontSize: 11, fontFamily: "var(--font-mono)" }}>Runs /</span>
            <span
              title={threadId}
              style={{
                fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)",
                background: "var(--bg-raised)", padding: "2px 8px", borderRadius: 4,
                border: "1px solid var(--border)",
              }}
            >
              {threadId.length > 16 ? `${threadId.slice(0, 8)}…${threadId.slice(-6)}` : threadId}
            </span>
            {thread && <StatusBadge status={thread.status} />}

            {isLive && (
              <span className="inline-flex items-center gap-1.5 shrink-0 ml-auto" style={{
                fontSize: 10, fontWeight: 600, color: "var(--green)",
                background: "var(--green-dim)", border: "1px solid rgba(74,222,128,0.2)",
                borderRadius: 4, padding: "2px 7px",
              }}>
                <span className="rounded-full" style={{ width: 5, height: 5, background: "var(--green)", animation: "pulse-dot 2s ease-in-out infinite" }} />
                LIVE
              </span>
            )}
          </div>

          {/* Stats line */}
          <StatsLines
            thread={thread}
            stats={stats}
            nodes={nodes}
            agentList={agentList}
            isRunning={isRunning}
            liveMs={liveMs}
          />
        </header>

        {/* Content */}
        {loading && (
          <div style={{ padding: 32 }} className="flex flex-col gap-4">
            <div className="skeleton" style={{ height: 200, borderRadius: 8 }} />
            <div className="skeleton" style={{ height: 300, borderRadius: 8 }} />
          </div>
        )}

        {!loading && error && (
          <div style={{ padding: 32 }}>
            <div style={{ padding: 24, borderRadius: 8, background: "var(--red-dim)", border: "1px solid rgba(239,68,68,0.15)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--red)" }}>{error}</div>
              <Link href="/runs" className="no-underline" style={{ fontSize: 12, color: "var(--blue)", marginTop: 8, display: "inline-block" }}>
                &larr; Back to runs
              </Link>
            </div>
          </div>
        )}

        {!loading && !error && thread && (
          <div style={{ padding: "24px 32px" }}>
            {/* Tab switcher */}
            <div className="flex items-center gap-1" style={{ marginBottom: 16 }}>
              {(["waterfall", "timeline"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className="cursor-pointer"
                  style={{
                    padding: "6px 14px", borderRadius: 6, border: "none",
                    fontSize: 13, fontWeight: tab === t ? 500 : 400,
                    background: tab === t ? "var(--bg-raised)" : "transparent",
                    color: tab === t ? "var(--text)" : "var(--text-muted)",
                  }}
                >
                  {t === "waterfall" ? "Waterfall" : "Timeline"}
                </button>
              ))}
            </div>

            {/* Waterfall / Timeline + Detail side by side */}
            <div className="flex gap-6" style={{ alignItems: "flex-start" }}>
              {/* Left: chart */}
              <div style={{ flex: 1, minWidth: 0 }}>
                {tab === "waterfall" ? (
                  <WaterfallChart
                    nodes={nodes}
                    totalMs={totalMs}
                    liveMs={liveMs}
                    isRunning={isRunning}
                    selectedId={selectedNode?.id ?? null}
                    onSelect={setSelectedNode}
                  />
                ) : (
                  <Timeline
                    nodes={nodes}
                    selectedId={selectedNode?.id ?? null}
                    onSelect={setSelectedNode}
                  />
                )}
              </div>

              {/* Right: detail panel */}
              <div
                style={{
                  width: 380, flexShrink: 0,
                  background: "var(--bg-surface)", borderRadius: 8,
                  border: "1px solid var(--border)",
                  position: "sticky", top: 16,
                  maxHeight: "calc(100vh - 32px)",
                  overflow: "hidden",
                }}
              >
                <EventDetail node={selectedNode} />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
