"use client";

import { useState } from "react";
import type { ApprovalItem } from "@/lib/api";
import { approveAction, rejectAction } from "@/lib/api";
import { formatRelTime } from "@/lib/utils";

interface Props {
  approvals: ApprovalItem[];
  onClose: () => void;
  onResolved: () => void;
}

export default function ApprovalDialog({ approvals, onClose, onResolved }: Props) {
  const [currentIdx, setCurrentIdx] = useState(0);
  const [acting, setActing] = useState(false);

  const item = approvals[currentIdx];
  if (!item) return null;

  const toolCalls = item.pending_tool_calls ?? [];

  async function handleAction(action: "approve" | "reject") {
    setActing(true);
    try {
      if (action === "approve") {
        await approveAction(item.trace_id, item.step_index);
      } else {
        await rejectAction(item.trace_id, item.step_index);
      }
      onResolved();
      // Move to next or close
      if (approvals.length <= 1) {
        onClose();
      } else if (currentIdx >= approvals.length - 1) {
        setCurrentIdx(Math.max(0, currentIdx - 1));
      }
    } catch {
      // Approval may have already been resolved
      onResolved();
    } finally {
      setActing(false);
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.6)",
          backdropFilter: "blur(4px)",
          zIndex: 200,
          animation: "backdrop-in 0.15s ease-out",
        }}
      />

      {/* Dialog */}
      <div
        style={{
          position: "fixed",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "100%",
          maxWidth: 520,
          background: "var(--bg-surface)",
          border: "1px solid var(--border-strong)",
          borderRadius: 12,
          zIndex: 201,
          overflow: "hidden",
          animation: "dialog-in 0.2s ease-out both",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between"
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div className="flex items-center gap-3">
            {/* Amber shield icon */}
            <div
              className="flex items-center justify-center shrink-0"
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: "var(--amber-dim)",
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 9v4M12 17h.01" />
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              </svg>
            </div>
            <div>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em" }}>
                Approval Required
              </h2>
              <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--text-muted)" }}>
                {approvals.length === 1
                  ? "An agent is waiting for your approval"
                  : `${approvals.length} agents waiting \u00b7 ${currentIdx + 1} of ${approvals.length}`}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="flex items-center justify-center cursor-pointer transition-colors"
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "transparent",
              color: "var(--text-muted)",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-raised)"; e.currentTarget.style.color = "var(--text)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px" }}>
          {/* Meta row */}
          <div
            className="flex items-center gap-4 flex-wrap"
            style={{
              marginBottom: 14,
              fontSize: 12,
              fontFamily: "var(--font-mono)",
              color: "var(--text-secondary)",
            }}
          >
            <span>
              <span style={{ color: "var(--text-dim)" }}>agent</span>{" "}
              {item.agent_id}
            </span>
            <span>
              <span style={{ color: "var(--text-dim)" }}>thread</span>{" "}
              {item.thread_id.length > 20 ? item.thread_id.slice(0, 20) + "\u2026" : item.thread_id}
            </span>
            <span style={{ color: "var(--text-dim)" }}>
              {formatRelTime(item.created_at)}
            </span>
          </div>

          {/* Tool calls */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>
              Pending tool calls
            </div>
            {toolCalls.length === 0 ? (
              <div
                style={{
                  padding: "12px",
                  borderRadius: 8,
                  background: "var(--bg-raised)",
                  border: "1px solid var(--border)",
                  fontSize: 12,
                  color: "var(--text-muted)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                No tool call details available
              </div>
            ) : (
              <div className="flex flex-col" style={{ gap: 8 }}>
                {toolCalls.map((tc, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "var(--code-bg)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div className="flex items-center gap-2" style={{ marginBottom: tc.arguments ? 6 : 0 }}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                      </svg>
                      <span
                        style={{
                          fontSize: 13,
                          fontWeight: 500,
                          fontFamily: "var(--font-mono)",
                          color: "var(--text)",
                        }}
                      >
                        {tc.name || `tool_call_${i}`}
                      </span>
                    </div>
                    {tc.arguments && (
                      <pre
                        style={{
                          margin: 0,
                          fontSize: 11.5,
                          fontFamily: "var(--font-mono)",
                          color: "var(--text-secondary)",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-all",
                          maxHeight: 120,
                          overflow: "auto",
                          lineHeight: 1.5,
                        }}
                      >
                        {(() => {
                          try {
                            return JSON.stringify(JSON.parse(tc.arguments), null, 2);
                          } catch {
                            return tc.arguments;
                          }
                        })()}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-between"
          style={{
            padding: "14px 20px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg-raised)",
          }}
        >
          {/* Pagination (if multiple) */}
          <div className="flex items-center gap-2">
            {approvals.length > 1 && (
              <>
                <button
                  disabled={currentIdx === 0}
                  onClick={() => setCurrentIdx((i) => i - 1)}
                  className="flex items-center justify-center cursor-pointer transition-colors"
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 5,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: currentIdx === 0 ? "var(--text-dim)" : "var(--text-secondary)",
                    fontSize: 14,
                    opacity: currentIdx === 0 ? 0.4 : 1,
                  }}
                >
                  &lsaquo;
                </button>
                <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
                  {currentIdx + 1}/{approvals.length}
                </span>
                <button
                  disabled={currentIdx >= approvals.length - 1}
                  onClick={() => setCurrentIdx((i) => i + 1)}
                  className="flex items-center justify-center cursor-pointer transition-colors"
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 5,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: currentIdx >= approvals.length - 1 ? "var(--text-dim)" : "var(--text-secondary)",
                    fontSize: 14,
                    opacity: currentIdx >= approvals.length - 1 ? 0.4 : 1,
                  }}
                >
                  &rsaquo;
                </button>
              </>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleAction("reject")}
              disabled={acting}
              className="cursor-pointer transition-colors"
              style={{
                padding: "7px 16px",
                borderRadius: 7,
                border: "1px solid rgba(239,68,68,0.25)",
                background: "var(--red-dim)",
                color: "var(--red)",
                fontSize: 13,
                fontWeight: 500,
                opacity: acting ? 0.5 : 1,
              }}
              onMouseEnter={(e) => { if (!acting) { e.currentTarget.style.background = "rgba(239,68,68,0.18)"; } }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "var(--red-dim)"; }}
            >
              Reject
            </button>
            <button
              onClick={() => handleAction("approve")}
              disabled={acting}
              className="cursor-pointer transition-colors"
              style={{
                padding: "7px 20px",
                borderRadius: 7,
                border: "1px solid rgba(74,222,128,0.3)",
                background: "rgba(74,222,128,0.12)",
                color: "var(--green)",
                fontSize: 13,
                fontWeight: 600,
                opacity: acting ? 0.5 : 1,
              }}
              onMouseEnter={(e) => { if (!acting) { e.currentTarget.style.background = "rgba(74,222,128,0.2)"; } }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(74,222,128,0.12)"; }}
            >
              Approve
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
