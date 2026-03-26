"use client";

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  getThread,
  getThreadTimeline,
  checkApiStatus,
  subscribeThreadStream,
  type ThreadSummary,
  type ApiStatus,
  type StreamUpdate,
} from "@/lib/api";
import { timelineEventsToTraceNodes, calcTotalMs, type TraceNode } from "@/lib/trace";
import StatusBadge from "@/components/status-badge";
import TracePanel from "@/components/trace-panel";
import { NodeIcon } from "@/components/node-icons";
import {
  formatDuration,
  formatTokens,
  formatRelTime,
  formatCost,
  shortModel,
} from "@/lib/utils";

/* ── Waterfall row ────────────────────────────────────────────────────────── */

const BAR_COLORS: Record<string, { bar: string; border: string }> = {
  llm:      { bar: "rgba(96,165,250,0.25)",  border: "rgba(96,165,250,0.5)" },
  tool:     { bar: "rgba(74,222,128,0.25)",  border: "rgba(74,222,128,0.5)" },
  thinking: { bar: "rgba(251,191,36,0.25)",  border: "rgba(251,191,36,0.5)" },
  handoff:  { bar: "rgba(167,139,250,0.25)", border: "rgba(167,139,250,0.5)" },
};

const DOT_COLORS: Record<string, string> = {
  llm: "#60A5FA", tool: "#4ADE80", thinking: "#FBBF24", handoff: "#A78BFA",
};

function WaterfallRow({
  node, totalMs, isSelected, onClick,
}: { node: TraceNode; totalMs: number; isSelected: boolean; onClick: () => void }) {
  const leftPct = (node.startOffsetMs / totalMs) * 100;
  const widthPct = node.durationMs ? Math.max((node.durationMs / totalMs) * 100, 0.5) : 5;
  const bc = BAR_COLORS[node.type] ?? BAR_COLORS.tool;
  const dotColor = DOT_COLORS[node.type] ?? DOT_COLORS.tool;

  return (
    <button
      onClick={onClick}
      className="flex items-center w-full text-left cursor-pointer transition-colors"
      style={{
        height: 36,
        background: isSelected ? "var(--bg-hover)" : "transparent",
        border: "none",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Label column */}
      <div
        className="flex items-center gap-2 shrink-0"
        style={{
          width: 240,
          padding: "0 10px",
          borderRight: "1px solid var(--border)",
          overflow: "hidden",
        }}
      >
        <NodeIcon type={node.type} size={13} color={dotColor} />
        <span
          className="truncate"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11.5,
            color: "var(--text-secondary)",
          }}
        >
          {node.label}
        </span>
        <span
          className="ml-auto shrink-0"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-dim)",
          }}
        >
          {node.durationMs != null ? formatDuration(node.durationMs) : "..."}
        </span>
      </div>

      {/* Bar column */}
      <div className="relative flex-1" style={{ height: "100%" }}>
        <div
          style={{
            position: "absolute",
            top: "50%",
            transform: "translateY(-50%)",
            height: 22,
            left: `${leftPct}%`,
            width: `${widthPct}%`,
            minWidth: 6,
            borderRadius: 4,
            background: bc.bar,
            border: `1px solid ${bc.border}`,
            animation: node.status === "running" ? "pulse-dot 2s ease-in-out infinite" : "none",
          }}
        />
      </div>
    </button>
  );
}

/* ── Detail panel ─────────────────────────────────────────────────────────── */

function DetailPanel({ node }: { node: TraceNode }) {
  return (
    <div style={{ padding: "16px 20px" }}>
      {/* Meta */}
      <div
        className="flex flex-wrap gap-x-5 gap-y-1"
        style={{
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          color: "var(--text-dim)",
          marginBottom: 12,
        }}
      >
        {node.model && <span>MODEL {node.model}</span>}
        {node.inputTokens != null && <span>IN {formatTokens(node.inputTokens)}</span>}
        {node.outputTokens != null && <span>OUT {formatTokens(node.outputTokens)}</span>}
        {node.estimatedCostUsd != null && (
          <span style={{ color: "var(--green)" }}>COST ${node.estimatedCostUsd.toFixed(4)}</span>
        )}
        <span>OFFSET {node.startOffsetMs}ms</span>
        {node.durationMs != null && <span>DURATION {formatDuration(node.durationMs)}</span>}
      </div>

      {/* Content */}
      {node.llmContent && (
        <>
          {node.llmContent.prompt && <ContentBlock title="Prompt" content={node.llmContent.prompt} />}
          {node.llmContent.response && <ContentBlock title="Response" content={node.llmContent.response} />}
        </>
      )}
      {node.toolContent && (
        <>
          {node.toolContent.input && <ContentBlock title="Input" content={node.toolContent.input} />}
          {node.toolContent.output && <ContentBlock title="Output" content={node.toolContent.output} />}
        </>
      )}
      {node.thinkingContent && <ContentBlock title="Reasoning" content={node.thinkingContent.text} />}
      {node.handoffContent && (
        <>
          <ContentBlock title="Target Agent" content={node.handoffContent.targetAgent} />
          <ContentBlock title="Message" content={node.handoffContent.message} />
        </>
      )}
    </div>
  );
}

function ContentBlock({ title, content }: { title: string; content: string }) {
  if (!content) return null;
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 600,
          color: "var(--text-dim)",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          marginBottom: 4,
        }}
      >
        {title}
      </div>
      <pre
        style={{
          margin: 0,
          padding: "10px 12px",
          borderRadius: 6,
          background: "var(--code-bg)",
          border: "1px solid var(--border)",
          fontFamily: "var(--font-mono)",
          fontSize: 11.5,
          lineHeight: 1.65,
          color: "var(--text-secondary)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 300,
          overflow: "auto",
        }}
      >
        {content}
      </pre>
    </div>
  );
}

/* ── Legend ────────────────────────────────────────────────────────────────── */

const LEGEND = [
  { type: "llm" as const, label: "LLM", color: "#60A5FA" },
  { type: "tool" as const, label: "Tool", color: "#4ADE80" },
  { type: "thinking" as const, label: "Think", color: "#FBBF24" },
  { type: "handoff" as const, label: "Handoff", color: "#A78BFA" },
];

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function ObservePage() {
  const params = useSearchParams();
  const threadId = params.get("thread_id") ?? "";

  const [thread, setThread] = useState<ThreadSummary | null>(null);
  const [nodes, setNodes] = useState<TraceNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"waterfall" | "trace">("waterfall");

  // Keep a ref to the latest thread so SSE callbacks never go stale
  const threadRef = useRef<ThreadSummary | null>(null);
  threadRef.current = thread;

  // Load thread summary + timeline together
  useEffect(() => {
    if (!threadId) { setLoading(false); return; }

    let cancelled = false;
    setLoading(true);

    // Fetch thread detail and timeline in parallel
    Promise.all([
      getThread(threadId),
      getThreadTimeline(threadId),
    ])
      .then(([threadData, events]) => {
        if (cancelled) return;
        setThread(threadData);
        setNodes(timelineEventsToTraceNodes(events, threadData));
      })
      .catch(() => {
        if (cancelled) return;
        // Timeline-only fallback if thread detail fails
        getThreadTimeline(threadId)
          .then((events) => {
            if (cancelled) return;
            const stub: ThreadSummary = {
              thread_id: threadId, agent_id: "", agent_name: "", agents: [],
              trace_id: "", status: "in_progress", step_index: 0,
              started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
              duration_ms: null, tokens_used: 0, model: "",
              trigger: "api", run_cost_usd: 0, budget_usd: 0,
              budget_soft_warned: false, rate_limited: false, rate_limit_waits: 0,
            };
            setNodes(timelineEventsToTraceNodes(events, stub));
          })
          .catch(() => {});
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    // SSE for live updates — uses threadRef to always read latest thread
    const cleanup = subscribeThreadStream(
      threadId,
      (update: StreamUpdate) => {
        const current = threadRef.current;
        if (update.events?.length && current) {
          setNodes(timelineEventsToTraceNodes(update.events, {
            ...current,
            status: update.status,
            step_index: update.step_index,
          }));
        }
      },
      () => {},
    );

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [threadId]);

  const totalMs = useMemo(() => calcTotalMs(nodes), [nodes]);
  const totalTokens = useMemo(
    () => nodes.reduce((s, n) => s + (n.inputTokens ?? 0) + (n.outputTokens ?? 0), 0),
    [nodes],
  );
  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;

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
    <div className="min-h-screen flex flex-col" style={{ background: "var(--bg)" }}>
      {/* Header bar */}
      <header
        className="flex items-center justify-between shrink-0"
        style={{
          padding: "12px 20px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-surface)",
        }}
      >
        <div className="flex items-center gap-3">
          <Link
            href="/runs"
            className="flex items-center justify-center no-underline transition-colors"
            style={{
              width: 28, height: 28, borderRadius: 6,
              border: "1px solid var(--border)", color: "var(--text-muted)",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
          </Link>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-muted)" }}>
            {threadId.length > 20 ? threadId.slice(0, 20) + "..." : threadId}
          </span>
          {thread && <StatusBadge status={thread.status} />}
          {thread && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
              {thread.agent_name || thread.agent_id}
              {thread.agents.length > 1 && ` +${thread.agents.length - 1}`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {totalTokens > 0 && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
              {formatTokens(totalTokens)}t
            </span>
          )}
          {thread && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
              {shortModel(thread.model)}
            </span>
          )}
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
            {formatDuration(totalMs)}
          </span>
        </div>
      </header>

      {loading ? (
        <div className="flex-1 flex items-center justify-center" style={{ color: "var(--text-muted)", fontSize: 13 }}>
          Loading trace...
        </div>
      ) : nodes.length === 0 ? (
        <div className="flex-1 flex items-center justify-center" style={{ color: "var(--text-muted)", fontSize: 13 }}>
          No trace events found.
        </div>
      ) : (
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* View toggle + legend */}
          <div
            className="flex items-center justify-between shrink-0"
            style={{ padding: "8px 20px", borderBottom: "1px solid var(--border)" }}
          >
            <div className="flex items-center gap-1">
              {(["waterfall", "trace"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => { setView(v); setSelectedId(null); }}
                  className="cursor-pointer transition-colors"
                  style={{
                    padding: "4px 12px",
                    borderRadius: 6,
                    border: "none",
                    background: view === v ? "var(--bg-raised)" : "transparent",
                    color: view === v ? "var(--text)" : "var(--text-muted)",
                    fontSize: 12,
                    fontWeight: view === v ? 500 : 400,
                  }}
                >
                  {v === "waterfall" ? "Waterfall" : "Timeline"}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3">
              {LEGEND.map((l) => (
                <span key={l.type} className="flex items-center gap-1.5">
                  <NodeIcon type={l.type} size={12} color={l.color} />
                  <span style={{ fontSize: 10.5, color: "var(--text-dim)" }}>{l.label}</span>
                </span>
              ))}
            </div>
          </div>

          {view === "waterfall" ? (
            <div className="flex flex-1 overflow-hidden">
              {/* Waterfall rows */}
              <div className="flex-1 overflow-auto">
                {/* Ruler */}
                <div
                  className="flex items-center shrink-0"
                  style={{
                    height: 28,
                    borderBottom: "1px solid var(--border)",
                    position: "sticky",
                    top: 0,
                    background: "var(--bg)",
                    zIndex: 5,
                  }}
                >
                  <div style={{ width: 240, borderRight: "1px solid var(--border)", padding: "0 10px" }}>
                    <span style={{ fontSize: 10, fontWeight: 600, color: "var(--text-dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
                      Step
                    </span>
                  </div>
                  <div className="flex-1 flex items-center justify-between" style={{ padding: "0 12px" }}>
                    {[0, 25, 50, 75, 100].map((pct) => (
                      <span key={pct} style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                        {pct === 0 ? "0" : formatDuration(Math.round(totalMs * pct / 100))}
                      </span>
                    ))}
                  </div>
                </div>

                {nodes.map((node) => (
                  <WaterfallRow
                    key={node.id}
                    node={node}
                    totalMs={totalMs}
                    isSelected={selectedId === node.id}
                    onClick={() => setSelectedId(selectedId === node.id ? null : node.id)}
                  />
                ))}
              </div>

              {/* Detail sidebar */}
              {selectedNode && (
                <div
                  className="shrink-0 overflow-auto"
                  style={{
                    width: 400,
                    borderLeft: "1px solid var(--border)",
                    background: "var(--bg-surface)",
                  }}
                >
                  <div
                    className="flex items-center justify-between"
                    style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)" }}
                  >
                    <div className="flex items-center gap-2">
                      <NodeIcon type={selectedNode.type} size={14} color={DOT_COLORS[selectedNode.type]} />
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 500, color: "var(--text)" }}>
                        {selectedNode.label}
                      </span>
                    </div>
                    <button
                      onClick={() => setSelectedId(null)}
                      className="cursor-pointer"
                      style={{ background: "none", border: "none", color: "var(--text-muted)", fontSize: 16 }}
                    >
                      &times;
                    </button>
                  </div>
                  <DetailPanel node={selectedNode} />
                </div>
              )}
            </div>
          ) : (
            /* Timeline (trace tree) view */
            <div style={{ padding: "8px 0", overflow: "auto", flex: 1 }}>
              <TracePanel
                nodes={nodes}
                threadId={threadId}
                agentId={thread?.agent_name || thread?.agent_id || ""}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
