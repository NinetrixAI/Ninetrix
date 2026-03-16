"use client";

export function normalizeStatus(
  s: string
): "running" | "completed" | "error" | "pending" | "cancelled" | "budget" {
  if (s === "in_progress" || s === "started" || s === "running") return "running";
  if (s === "completed" || s === "approved") return "completed";
  if (s === "error" || s === "failed") return "error";
  if (s === "waiting_for_approval" || s === "pending") return "pending";
  if (s === "budget_exceeded") return "budget";
  return "cancelled";
}

export const STATUS_STYLES: Record<
  string,
  { bg: string; text: string; dot: string; label: string }
> = {
  running:   { bg: "rgba(16,185,129,0.1)",  text: "#10B981", dot: "#10B981", label: "Running"   },
  completed: { bg: "rgba(59,130,246,0.12)", text: "#60A5FA", dot: "#3B82F6", label: "Completed" },
  error:     { bg: "rgba(239,68,68,0.1)",   text: "#EF4444", dot: "#EF4444", label: "Failed"    },
  pending:   { bg: "rgba(245,158,11,0.1)",  text: "#F59E0B", dot: "#F59E0B", label: "Pending"   },
  cancelled: { bg: "rgba(100,116,139,0.08)",text: "#64748B", dot: "#475569", label: "Cancelled" },
  budget:    { bg: "rgba(249,115,22,0.1)",  text: "#FB923C", dot: "#F97316", label: "Budget Exceeded" },
};

export default function StatusBadge({
  status,
  size = "sm",
}: {
  status: string;
  size?: "sm" | "md";
}) {
  const norm = normalizeStatus(status);
  const s = STATUS_STYLES[norm] ?? STATUS_STYLES.cancelled;
  const isRunning = norm === "running";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size === "md" ? 6 : 5,
        padding: size === "md" ? "3px 10px" : "2px 8px",
        borderRadius: 4,
        background: s.bg,
        color: s.text,
        fontSize: size === "md" ? 12 : 11,
        fontWeight: 500,
        letterSpacing: "0.02em",
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: size === "md" ? 6 : 5,
          height: size === "md" ? 6 : 5,
          borderRadius: "50%",
          background: s.dot,
          flexShrink: 0,
          animation: isRunning ? "pulse-ring 1.6s ease-in-out infinite" : "none",
        }}
      />
      {s.label}
    </span>
  );
}
