"use client";

import { formatCost } from "@/lib/utils";

/* ── Thresholds ─────────────────────────────────────────────────────────── */

function budgetState(spent: number, budget: number, softWarned: boolean) {
  if (budget <= 0) return { level: "none" as const, pct: 0 };
  const pct = Math.min((spent / budget) * 100, 100);
  if (spent >= budget) return { level: "exceeded" as const, pct: 100 };
  if (pct >= 80) return { level: "critical" as const, pct };
  if (softWarned || pct >= 50) return { level: "warning" as const, pct };
  return { level: "normal" as const, pct };
}

const COLORS: Record<string, { bar: string; bg: string; text: string }> = {
  none:     { bar: "var(--purple)", bg: "var(--purple-dim)", text: "var(--text-secondary)" },
  normal:   { bar: "var(--purple)", bg: "var(--purple-dim)", text: "var(--purple)" },
  warning:  { bar: "var(--amber)",  bg: "var(--amber-dim)",  text: "var(--amber)" },
  critical: { bar: "var(--orange)", bg: "var(--orange-dim)", text: "var(--orange)" },
  exceeded: { bar: "var(--red)",    bg: "var(--red-dim)",    text: "var(--red)" },
};

/* ── Inline budget bar (for runs table) ─────────────────────────────────── */

export function BudgetInline({
  spent,
  budget,
  softWarned,
}: {
  spent: number;
  budget: number;
  softWarned: boolean;
}) {
  if (budget <= 0) {
    // No budget set — just show cost
    return (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12.5,
          color: spent > 0 ? "var(--text-secondary)" : "var(--text-dim)",
        }}
      >
        {spent > 0 ? formatCost(spent) : "--"}
      </span>
    );
  }

  const { level, pct } = budgetState(spent, budget, softWarned);
  const c = COLORS[level];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 80 }}>
      {/* Cost text */}
      <div className="flex items-baseline justify-between gap-2">
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            fontWeight: 500,
            color: c.text,
          }}
        >
          {formatCost(spent)}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text-dim)",
          }}
        >
          /{formatCost(budget)}
        </span>
      </div>
      {/* Progress bar */}
      <div
        style={{
          height: 3,
          borderRadius: 2,
          background: "var(--bg-raised)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            borderRadius: 2,
            background: c.bar,
            transition: "width 0.3s ease",
          }}
        />
      </div>
    </div>
  );
}

