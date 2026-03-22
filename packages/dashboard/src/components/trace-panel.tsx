"use client";

import React, { useState, useMemo } from "react";
import { Highlight, type PrismTheme } from "prism-react-renderer";
import type { TraceNode } from "@/lib/trace";
import { formatDuration, formatTokens, formatTimestamp } from "@/lib/utils";
import { calcTotalMs } from "@/lib/trace";
import { NodeIcon } from "@/components/node-icons";

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

/* ── Node styling ────────────────────────────────────────────────────────── */

const NODE_COLORS: Record<string, { dot: string; bar: string; label: string }> = {
  llm:      { dot: "#60A5FA", bar: "rgba(96,165,250,0.25)",  label: "llm call" },
  tool:     { dot: "#4ADE80", bar: "rgba(74,222,128,0.25)",  label: "tool call" },
  thinking: { dot: "#FBBF24", bar: "rgba(251,191,36,0.25)",  label: "thinking" },
  handoff:  { dot: "#A78BFA", bar: "rgba(167,139,250,0.25)", label: "handoff" },
};

function nodeLabel(n: TraceNode): string {
  const c = NODE_COLORS[n.type] ?? NODE_COLORS.tool;
  if (n.type === "llm") return `${c.label} \u00b7 ${n.model ?? n.label}`;
  if (n.type === "handoff") return `${c.label} \u00b7 ${n.handoffContent?.targetAgent ?? n.label}`;
  return `${c.label} \u00b7 ${n.label}`;
}

function DurationBadge({ ms, status }: { ms: number | null; status: string }) {
  if (ms == null && status !== "running") return null;

  const bg = status === "running"
    ? "rgba(74,222,128,0.15)"
    : status === "error"
    ? "rgba(239,68,68,0.15)"
    : ms != null && ms > 2000
    ? "rgba(251,191,36,0.15)"
    : "rgba(74,222,128,0.15)";

  const color = status === "running"
    ? "#4ADE80"
    : status === "error"
    ? "#EF4444"
    : ms != null && ms > 2000
    ? "#FBBF24"
    : "#4ADE80";

  return (
    <span
      className="inline-flex items-center rounded"
      style={{
        padding: "1px 6px",
        background: bg,
        color,
        fontSize: 11,
        fontWeight: 500,
        fontFamily: "var(--font-mono)",
        lineHeight: "18px",
        animation: status === "running" ? "pulse-dot 2s ease-in-out infinite" : "none",
      }}
    >
      {status === "running" ? "running" : formatDuration(ms)}
    </span>
  );
}

/* ── Content viewer ──────────────────────────────────────────────────────── */

function ContentBlock({ title, content }: { title: string; content: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!content) return null;

  const isJson = content.trimStart().startsWith("{") || content.trimStart().startsWith("[");
  let formatted = content;
  if (isJson) {
    try { formatted = JSON.stringify(JSON.parse(content), null, 2); } catch { /* keep raw */ }
  }

  const lines = formatted.split("\n");
  const showToggle = lines.length > 12;
  const displayContent = showToggle && !expanded ? lines.slice(0, 12).join("\n") + "\n..." : formatted;

  return (
    <div style={{ marginTop: 8 }}>
      <div
        className="flex items-center justify-between"
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
        {showToggle && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="cursor-pointer"
            style={{
              background: "none",
              border: "none",
              color: "var(--blue)",
              fontSize: 10,
              fontWeight: 500,
              letterSpacing: "normal",
              textTransform: "none",
            }}
          >
            {expanded ? "collapse" : "expand"}
          </button>
        )}
      </div>
      <div
        style={{
          background: "var(--code-bg)",
          borderRadius: 6,
          border: "1px solid var(--border)",
          padding: "10px 12px",
          overflow: "auto",
          maxHeight: expanded ? "none" : 260,
        }}
      >
        {isJson ? (
          <Highlight theme={JSON_THEME} code={displayContent} language="json">
            {({ tokens, getLineProps, getTokenProps }) => (
              <pre
                style={{
                  margin: 0,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11.5,
                  lineHeight: 1.65,
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
              fontFamily: "var(--font-mono)",
              fontSize: 11.5,
              lineHeight: 1.65,
              color: "var(--text-secondary)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {displayContent}
          </pre>
        )}
      </div>
    </div>
  );
}

/* ── Detail panel ────────────────────────────────────────────────────────── */

function NodeDetail({ node }: { node: TraceNode }) {
  return (
    <div className="animate-fade-in" style={{ padding: "12px 16px" }}>
      {/* Metadata */}
      <div
        className="flex flex-wrap gap-x-5 gap-y-1"
        style={{
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          color: "var(--text-dim)",
          marginBottom: 8,
        }}
      >
        {node.model && <span>MODEL {node.model}</span>}
        {node.inputTokens != null && <span>IN {formatTokens(node.inputTokens)}</span>}
        {node.outputTokens != null && <span>OUT {formatTokens(node.outputTokens)}</span>}
        {node.estimatedCostUsd != null && (
          <span style={{ color: "var(--green)" }}>
            COST ${node.estimatedCostUsd.toFixed(4)}
          </span>
        )}
        <span>AT {formatTimestamp(node.absoluteStartIso)}</span>
      </div>

      {/* Content blocks */}
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
      {node.thinkingContent && (
        <ContentBlock title="Reasoning" content={node.thinkingContent.text} />
      )}
      {node.handoffContent && (
        <>
          <ContentBlock title="Target Agent" content={node.handoffContent.targetAgent} />
          <ContentBlock title="Message" content={node.handoffContent.message} />
        </>
      )}
    </div>
  );
}

/* ── Main trace panel ────────────────────────────────────────────────────── */

interface TracePanelProps {
  nodes: TraceNode[];
  threadId: string;
  agentId: string;
}

export default function TracePanel({ nodes, threadId, agentId }: TracePanelProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const totalMs = useMemo(() => calcTotalMs(nodes), [nodes]);
  const selectedNode = nodes.find((n) => n.id === selectedId);

  const totalTokens = useMemo(
    () => nodes.reduce((sum, n) => sum + (n.inputTokens ?? 0) + (n.outputTokens ?? 0), 0),
    [nodes],
  );

  if (nodes.length === 0) {
    return (
      <div
        className="animate-slide-in flex items-center gap-3"
        style={{
          background: "var(--bg-surface)",
          borderTop: "1px solid var(--border)",
          padding: "20px 24px",
        }}
      >
        <span
          className="shrink-0 rounded-full"
          style={{
            width: 6,
            height: 6,
            background: "var(--purple)",
            animation: "pulse-dot 2s ease-in-out infinite",
          }}
        />
        <span
          style={{
            fontSize: 12.5,
            fontFamily: "var(--font-mono)",
            color: "var(--text-muted)",
          }}
        >
          Waiting for events...
        </span>
      </div>
    );
  }

  return (
    <div
      className="animate-slide-in"
      style={{
        background: "var(--bg-surface)",
        borderTop: "1px solid var(--border)",
        borderRadius: "0 0 8px 8px",
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between"
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div className="flex items-center gap-2">
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 500,
              color: "var(--text-secondary)",
            }}
          >
            Trace
          </span>
          <span style={{ color: "var(--text-dim)", fontSize: 12 }}>&middot;</span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              color: "var(--text-muted)",
            }}
          >
            {threadId.length > 16 ? threadId.slice(0, 16) + "..." : threadId}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {totalTokens > 0 && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--text-dim)",
              }}
            >
              {formatTokens(totalTokens)}t
            </span>
          )}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            {formatDuration(totalMs)} total
          </span>
        </div>
      </div>

      {/* Tree */}
      <div style={{ padding: "6px 0" }}>
        {/* Root agent node */}
        <div
          className="flex items-center justify-between"
          style={{
            padding: "5px 16px",
            fontSize: 13,
            fontFamily: "var(--font-mono)",
          }}
        >
          <div className="flex items-center gap-2">
            <span className="rounded-full" style={{ width: 6, height: 6, background: "var(--green)", flexShrink: 0 }} />
            <span style={{ color: "var(--text)", fontWeight: 500 }}>
              agent run &middot; {agentId}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {totalTokens > 0 && (
              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {formatTokens(totalTokens)}t
              </span>
            )}
            <DurationBadge ms={totalMs} status="success" />
          </div>
        </div>

        {/* Child nodes */}
        {nodes.map((node, i) => {
          const c = NODE_COLORS[node.type] ?? NODE_COLORS.tool;
          const isLast = i === nodes.length - 1;
          const isSelected = selectedId === node.id;
          const tokens = (node.inputTokens ?? 0) + (node.outputTokens ?? 0);
          // When expanded and not last, the next node's connector still needs the vertical trunk line
          const hasNodeBelow = !isLast;

          return (
            <React.Fragment key={node.id}>
              {/* Row wrapper with continuous tree line */}
              <div style={{ position: "relative" }}>
                {/* Vertical trunk line (from parent) — runs full height of this row + detail if expanded */}
                {!isLast && (
                  <div
                    style={{
                      position: "absolute",
                      left: 43,
                      top: 0,
                      bottom: 0,
                      width: 1,
                      background: "var(--border)",
                      pointerEvents: "none",
                      zIndex: 1,
                    }}
                  />
                )}
                {/* Horizontal branch to the dot */}
                <div
                  style={{
                    position: "absolute",
                    left: 43,
                    top: 14,
                    width: 12,
                    height: 1,
                    background: "var(--border)",
                    pointerEvents: "none",
                    zIndex: 1,
                  }}
                />
                {/* For last item: vertical stub from top to the branch point */}
                {isLast && (
                  <div
                    style={{
                      position: "absolute",
                      left: 43,
                      top: 0,
                      height: 15,
                      width: 1,
                      background: "var(--border)",
                      pointerEvents: "none",
                      zIndex: 1,
                    }}
                  />
                )}

                <button
                  onClick={() => setSelectedId(isSelected ? null : node.id)}
                  className="flex items-center justify-between w-full text-left cursor-pointer transition-colors"
                  style={{
                    padding: "4px 16px 4px 60px",
                    fontSize: 12.5,
                    fontFamily: "var(--font-mono)",
                    background: isSelected ? "var(--bg-hover)" : "transparent",
                    border: "none",
                    color: "var(--text)",
                    position: "relative",
                    zIndex: 2,
                  }}
                >
                  <div className="flex items-center gap-2" style={{ minWidth: 0 }}>
                    <span
                      className="shrink-0 flex items-center"
                      style={{
                        color: node.status === "error" ? "var(--red)" : c.dot,
                        animation: node.status === "running" ? "pulse-dot 2s ease-in-out infinite" : "none",
                      }}
                    >
                      <NodeIcon type={node.type} size={14} color={node.status === "error" ? "var(--red)" : c.dot} />
                    </span>
                    <span
                      className="truncate"
                      style={{
                        color: isSelected ? "var(--text)" : "var(--text-secondary)",
                        letterSpacing: "-0.01em",
                      }}
                    >
                      {nodeLabel(node)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-3">
                    {tokens > 0 && (
                      <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                        {formatTokens(tokens)}t
                      </span>
                    )}
                    <DurationBadge ms={node.durationMs} status={node.status} />
                  </div>
                </button>

                {/* Detail panel — inside the same wrapper so the trunk line continues through it */}
                {isSelected && selectedNode && (
                  <div style={{ marginLeft: 65, position: "relative", zIndex: 2 }}>
                    {/* Corner connector: curves from the trunk into the detail border */}
                    <div
                      style={{
                        position: "absolute",
                        left: -22,
                        top: 0,
                        width: 22,
                        height: 16,
                        borderLeft: "1px solid var(--border)",
                        borderBottom: "1px solid var(--border)",
                        borderBottomLeftRadius: 8,
                        pointerEvents: "none",
                      }}
                    />
                    <div
                      style={{
                        borderLeft: "1px solid var(--border)",
                        marginLeft: 0,
                        paddingLeft: 0,
                      }}
                    >
                      <NodeDetail node={selectedNode} />
                    </div>
                  </div>
                )}
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
