"use client";

import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  listThreads,
  listAgents,
  listChannels,
  getThreadTimeline,
  checkApiStatus,
  type ThreadSummary,
  type AgentStats,
  type ApiStatus,
  type Channel,
} from "@/lib/api";
import { timelineEventsToTraceNodes, calcTotalMs, type TraceNode } from "@/lib/trace";
import Sidebar from "@/components/sidebar";
import StatusBadge from "@/components/status-badge";
import { NodeIcon } from "@/components/node-icons";
import {
  formatDuration,
  formatTokens,
  formatCost,
  shortModel,
  normalizeStatus,
} from "@/lib/utils";

/* ── Node config ─────────────────────────────────────────────────────────── */

const NODE_CFG: Record<string, { color: string; dim: string; label: string }> = {
  llm:      { color: "#60A5FA", dim: "rgba(96,165,250,0.12)",  label: "LLM" },
  tool:     { color: "#4ADE80", dim: "rgba(74,222,128,0.12)",  label: "Tool" },
  thinking: { color: "#FBBF24", dim: "rgba(251,191,36,0.12)",  label: "Think" },
  handoff:  { color: "#A78BFA", dim: "rgba(167,139,250,0.12)", label: "Handoff" },
};

/* ── Delta indicator ─────────────────────────────────────────────────────── */

function Delta({ a, b, format, lowerBetter = true }: { a: number; b: number; format: (v: number) => string; lowerBetter?: boolean }) {
  const diff = b - a;
  if (diff === 0) return null;
  const better = lowerBetter ? diff < 0 : diff > 0;
  return (
    <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: better ? "var(--green)" : "var(--red)", marginLeft: 6 }}>
      {diff > 0 ? "+" : ""}{format(diff)}
    </span>
  );
}

/* ── Stat row ────────────────────────────────────────────────────────────── */

function StatRow({ label, valA, valB, delta }: { label: string; valA: string; valB: string; delta?: React.ReactNode }) {
  return (
    <div className="flex items-center" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
      <div style={{ width: 100, fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ flex: 1, fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
        {valA}
      </div>
      <div style={{ flex: 1, fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
        {valB}
        {delta}
      </div>
    </div>
  );
}

/* ── Timeline column ─────────────────────────────────────────────────────── */

function TimelineColumn({
  nodes,
  otherNodes,
  selectedId,
  onSelect,
  label,
}: {
  nodes: TraceNode[];
  otherNodes: TraceNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  label: string;
}) {
  const otherLabels = useMemo(() => new Set(otherNodes.map((n) => `${n.type}:${n.label}`)), [otherNodes]);

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 9.5, fontWeight: 600, letterSpacing: "0.08em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
        {nodes.map((node) => {
          const cfg = NODE_CFG[node.type] ?? NODE_CFG.tool;
          const isSelected = selectedId === node.id;
          const isError = node.status === "error";
          const isNew = !otherLabels.has(`${node.type}:${node.label}`);

          return (
            <div
              key={node.id}
              onClick={() => onSelect(node.id)}
              className="flex items-center gap-2 cursor-pointer transition-colors"
              style={{
                padding: "7px 12px",
                background: isSelected ? cfg.dim : "transparent",
                boxShadow: isSelected ? `inset 2px 0 0 ${cfg.color}` : "none",
                borderBottom: "1px solid var(--border)",
                borderRight: isNew ? "3px solid var(--amber)" : "none",
              }}
              onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
              onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = isSelected ? cfg.dim : "transparent"; }}
            >
              <div
                className="flex items-center justify-center shrink-0"
                style={{ width: 22, height: 22, borderRadius: 4, background: isError ? "var(--red-dim)" : cfg.dim }}
              >
                <NodeIcon type={node.type} size={12} color={isError ? "var(--red)" : cfg.color} />
              </div>
              <span className="truncate" style={{ fontSize: 11.5, fontFamily: "var(--font-mono)", fontWeight: isSelected ? 500 : 400, color: isError ? "var(--red)" : "var(--text)" }}>
                {node.label}
              </span>
              <span style={{ fontSize: 10, color: cfg.color, background: cfg.dim, padding: "0 5px", borderRadius: 3, flexShrink: 0 }}>
                {cfg.label.toLowerCase()}
              </span>
              <span className="ml-auto shrink-0" style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                {node.durationMs != null ? formatDuration(node.durationMs) : "..."}
              </span>
            </div>
          );
        })}
        {nodes.length === 0 && (
          <div className="flex items-center justify-center" style={{ height: 60, color: "var(--text-dim)", fontSize: 12 }}>
            No events
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Detail panel ────────────────────────────────────────────────────────── */

function DetailPanel({ node }: { node: TraceNode | null }) {
  if (!node) return null;
  const cfg = NODE_CFG[node.type] ?? NODE_CFG.tool;

  return (
    <div className="animate-fade-in" style={{ padding: "12px 16px", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, marginTop: 16 }}>
      <div className="flex items-center gap-2" style={{ marginBottom: 10 }}>
        <NodeIcon type={node.type} size={14} color={cfg.color} />
        <span style={{ fontSize: 13, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>{node.label}</span>
        <span style={{ fontSize: 10, color: cfg.color, background: cfg.dim, padding: "1px 6px", borderRadius: 3 }}>{cfg.label}</span>
        {node.model && <span style={{ fontSize: 10, color: "var(--text-dim)", background: "var(--bg-raised)", padding: "1px 6px", borderRadius: 3, border: "1px solid var(--border)" }}>{shortModel(node.model)}</span>}
      </div>
      <div className="flex flex-wrap gap-4" style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--text-dim)", marginBottom: 10 }}>
        {node.durationMs != null && <span>Duration: {formatDuration(node.durationMs)}</span>}
        {node.inputTokens != null && <span>In: {node.inputTokens}</span>}
        {node.outputTokens != null && <span>Out: {node.outputTokens}</span>}
        {node.estimatedCostUsd != null && <span>Cost: ${node.estimatedCostUsd.toFixed(5)}</span>}
      </div>
      {node.llmContent?.prompt && <ContentBlock title="Prompt" content={node.llmContent.prompt} />}
      {node.llmContent?.response && <ContentBlock title="Response" content={node.llmContent.response} />}
      {node.toolContent?.input && <ContentBlock title="Input" content={node.toolContent.input} />}
      {node.toolContent?.output && <ContentBlock title="Output" content={node.toolContent.output} />}
      {node.thinkingContent?.text && <ContentBlock title="Reasoning" content={node.thinkingContent.text} />}
      {node.handoffContent?.targetAgent && <ContentBlock title="Target" content={node.handoffContent.targetAgent} />}
    </div>
  );
}

function ContentBlock({ title, content }: { title: string; content: string }) {
  if (!content) return null;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.06em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 3 }}>{title}</div>
      <pre style={{
        margin: 0, padding: "8px 10px", borderRadius: 6, background: "var(--bg-raised)", border: "1px solid var(--border)",
        fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.6, color: "var(--text-secondary)",
        whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 200, overflow: "auto",
      }}>
        {content}
      </pre>
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────────────────── */

export default function CompareClient() {
  const params = useSearchParams();
  const idA = params.get("a") ?? "";
  const idB = params.get("b") ?? "";

  const [threadA, setThreadA] = useState<ThreadSummary | null>(null);
  const [threadB, setThreadB] = useState<ThreadSummary | null>(null);
  const [nodesA, setNodesA] = useState<TraceNode[]>([]);
  const [nodesB, setNodesB] = useState<TraceNode[]>([]);
  const [loading, setLoading] = useState(true);

  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Scroll sync
  const scrollRefA = useRef<HTMLDivElement>(null);
  const scrollRefB = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);

  const handleScrollA = useCallback(() => {
    if (syncing.current) return;
    syncing.current = true;
    if (scrollRefB.current && scrollRefA.current) {
      scrollRefB.current.scrollTop = scrollRefA.current.scrollTop;
    }
    syncing.current = false;
  }, []);

  const handleScrollB = useCallback(() => {
    if (syncing.current) return;
    syncing.current = true;
    if (scrollRefA.current && scrollRefB.current) {
      scrollRefA.current.scrollTop = scrollRefB.current.scrollTop;
    }
    syncing.current = false;
  }, []);

  const fetchChannels = useCallback(() => {
    listChannels().then(setChannels).catch(() => {});
  }, []);

  useEffect(() => {
    listAgents({ limit: 100 }).then((d) => setAgents(d.items ?? [])).catch(() => {});
    checkApiStatus().then(setApiStatus);
    fetchChannels();
  }, [fetchChannels]);

  // Load both threads and their timelines
  useEffect(() => {
    if (!idA || !idB) { setLoading(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const { items } = await listThreads({ limit: 200 });
        const foundA = items.find((t) => t.thread_id === idA);
        const foundB = items.find((t) => t.thread_id === idB);
        if (cancelled) return;
        if (foundA) {
          setThreadA(foundA);
          const evA = await getThreadTimeline(idA);
          if (!cancelled) setNodesA(timelineEventsToTraceNodes(evA, foundA));
        }
        if (foundB) {
          setThreadB(foundB);
          const evB = await getThreadTimeline(idB);
          if (!cancelled) setNodesB(timelineEventsToTraceNodes(evB, foundB));
        }
      } catch { /* ignore */ }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [idA, idB]);

  const totalMsA = useMemo(() => calcTotalMs(nodesA), [nodesA]);
  const totalMsB = useMemo(() => calcTotalMs(nodesB), [nodesB]);

  const statsA = useMemo(() => ({
    tokens: nodesA.reduce((s, n) => s + (n.inputTokens ?? 0) + (n.outputTokens ?? 0), 0),
    cost: nodesA.reduce((s, n) => s + (n.estimatedCostUsd ?? 0), 0),
    llm: nodesA.filter((n) => n.type === "llm").length,
    tools: nodesA.filter((n) => n.type === "tool").length,
    errors: nodesA.filter((n) => n.status === "error").length,
  }), [nodesA]);

  const statsB = useMemo(() => ({
    tokens: nodesB.reduce((s, n) => s + (n.inputTokens ?? 0) + (n.outputTokens ?? 0), 0),
    cost: nodesB.reduce((s, n) => s + (n.estimatedCostUsd ?? 0), 0),
    llm: nodesB.filter((n) => n.type === "llm").length,
    tools: nodesB.filter((n) => n.type === "tool").length,
    errors: nodesB.filter((n) => n.status === "error").length,
  }), [nodesB]);

  const selectedNode = [...nodesA, ...nodesB].find((n) => n.id === selectedNodeId) ?? null;

  if (!idA || !idB) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
        <div style={{ color: "var(--text-muted)", fontSize: 14 }}>
          Select two runs to compare.{" "}
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
        <header style={{ padding: "16px 32px", borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <Link
              href="/runs"
              className="inline-flex items-center justify-center no-underline transition-colors shrink-0"
              style={{ width: 26, height: 26, borderRadius: 6, border: "1px solid var(--border)", color: "var(--text-muted)" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-raised)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M19 12H5M12 19l-7-7 7-7" />
              </svg>
            </Link>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text)" }}>Compare Runs</span>
            <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
              {idA.slice(0, 8)}…
            </span>
            <span style={{ fontSize: 11, color: "var(--text-dim)" }}>vs</span>
            <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
              {idB.slice(0, 8)}…
            </span>
          </div>
        </header>

        {loading ? (
          <div style={{ padding: 32 }} className="flex gap-6">
            <div className="skeleton" style={{ flex: 1, height: 400, borderRadius: 8 }} />
            <div className="skeleton" style={{ flex: 1, height: 400, borderRadius: 8 }} />
          </div>
        ) : (
          <div style={{ padding: "20px 32px" }}>
            {/* Stats comparison */}
            <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 20px", marginBottom: 20 }}>
              {/* Column headers */}
              <div className="flex items-center" style={{ padding: "0 0 8px", borderBottom: "1px solid var(--border)", marginBottom: 4 }}>
                <div style={{ width: 100 }} />
                <div style={{ flex: 1, fontSize: 11, fontWeight: 600, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
                  Run A — {idA.slice(0, 10)}…
                  {threadA && <StatusBadge status={threadA.status} />}
                </div>
                <div style={{ flex: 1, fontSize: 11, fontWeight: 600, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
                  Run B — {idB.slice(0, 10)}…
                  {threadB && <StatusBadge status={threadB.status} />}
                </div>
              </div>

              <StatRow
                label="Agent"
                valA={threadA?.agent_name || threadA?.agent_id || "--"}
                valB={threadB?.agent_name || threadB?.agent_id || "--"}
              />
              <StatRow
                label="Model"
                valA={threadA?.model ? shortModel(threadA.model) : "--"}
                valB={threadB?.model ? shortModel(threadB.model) : "--"}
              />
              <StatRow
                label="Duration"
                valA={threadA?.duration_ms != null ? formatDuration(threadA.duration_ms) : formatDuration(totalMsA)}
                valB={threadB?.duration_ms != null ? formatDuration(threadB.duration_ms) : formatDuration(totalMsB)}
                delta={<Delta a={totalMsA} b={totalMsB} format={formatDuration} />}
              />
              <StatRow
                label="Tokens"
                valA={formatTokens(statsA.tokens)}
                valB={formatTokens(statsB.tokens)}
                delta={<Delta a={statsA.tokens} b={statsB.tokens} format={formatTokens} />}
              />
              <StatRow
                label="Cost"
                valA={statsA.cost > 0 ? formatCost(statsA.cost) : "--"}
                valB={statsB.cost > 0 ? formatCost(statsB.cost) : "--"}
                delta={statsA.cost > 0 && statsB.cost > 0 ? <Delta a={statsA.cost} b={statsB.cost} format={formatCost} /> : undefined}
              />
              <StatRow
                label="LLM Calls"
                valA={String(statsA.llm)}
                valB={String(statsB.llm)}
                delta={<Delta a={statsA.llm} b={statsB.llm} format={(v) => String(v)} />}
              />
              <StatRow
                label="Tool Calls"
                valA={String(statsA.tools)}
                valB={String(statsB.tools)}
                delta={<Delta a={statsA.tools} b={statsB.tools} format={(v) => String(v)} />}
              />
              {(statsA.errors > 0 || statsB.errors > 0) && (
                <StatRow
                  label="Errors"
                  valA={String(statsA.errors)}
                  valB={String(statsB.errors)}
                  delta={<Delta a={statsA.errors} b={statsB.errors} format={(v) => String(v)} />}
                />
              )}
            </div>

            {/* Timeline comparison */}
            <div className="flex gap-4" style={{ alignItems: "flex-start" }}>
              <div ref={scrollRefA} onScroll={handleScrollA} style={{ flex: 1, maxHeight: "calc(100vh - 340px)", overflow: "auto" }}>
                <TimelineColumn
                  nodes={nodesA}
                  otherNodes={nodesB}
                  selectedId={selectedNodeId}
                  onSelect={(id) => setSelectedNodeId(selectedNodeId === id ? null : id)}
                  label={`Run A — ${nodesA.length} events`}
                />
              </div>
              <div ref={scrollRefB} onScroll={handleScrollB} style={{ flex: 1, maxHeight: "calc(100vh - 340px)", overflow: "auto" }}>
                <TimelineColumn
                  nodes={nodesB}
                  otherNodes={nodesA}
                  selectedId={selectedNodeId}
                  onSelect={(id) => setSelectedNodeId(selectedNodeId === id ? null : id)}
                  label={`Run B — ${nodesB.length} events`}
                />
              </div>
            </div>

            {/* Detail panel */}
            <DetailPanel node={selectedNode} />
          </div>
        )}
      </main>
    </div>
  );
}
