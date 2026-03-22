"use client";

import { normalizeStatus } from "@/lib/utils";

const STYLES: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  running:   { bg: "rgba(74,222,128,0.1)",   text: "#4ADE80", dot: "#4ADE80", label: "running" },
  completed: { bg: "rgba(96,165,250,0.1)",   text: "#60A5FA", dot: "#60A5FA", label: "completed" },
  idle:      { bg: "rgba(167,139,250,0.1)",  text: "#A78BFA", dot: "#A78BFA", label: "idle" },
  error:     { bg: "rgba(239,68,68,0.1)",    text: "#EF4444", dot: "#EF4444", label: "failed" },
  pending:   { bg: "rgba(251,191,36,0.1)",   text: "#FBBF24", dot: "#FBBF24", label: "pending" },
  cancelled: { bg: "rgba(100,116,139,0.08)", text: "#64748B", dot: "#475569", label: "cancelled" },
  budget:    { bg: "rgba(249,115,22,0.1)",   text: "#FB923C", dot: "#F97316", label: "budget" },
};

export default function StatusBadge({ status }: { status: string }) {
  const norm = normalizeStatus(status);
  const s = STYLES[norm] ?? STYLES.cancelled;
  const isRunning = norm === "running";

  return (
    <span
      className="inline-flex items-center gap-[5px] rounded-full whitespace-nowrap"
      style={{
        padding: "3px 10px",
        background: s.bg,
        color: s.text,
        fontSize: 12,
        fontWeight: 500,
        letterSpacing: "0.01em",
      }}
    >
      <span
        className="shrink-0 rounded-full"
        style={{
          width: 6,
          height: 6,
          background: s.dot,
          animation: isRunning ? "pulse-dot 2s ease-in-out infinite" : "none",
        }}
      />
      {s.label}
    </span>
  );
}
