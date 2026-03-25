"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  getAnalytics,
  listAgents,
  listChannels,
  checkApiStatus,
  type AnalyticsSummary,
  type DailyStats,
  type AgentStats,
  type ApiStatus,
  type Channel,
} from "@/lib/api";
import Sidebar from "@/components/sidebar";
import {
  formatDuration,
  formatTokens,
  formatCost,
  shortModel,
} from "@/lib/utils";

/* ── Stat card ──────────────────────────────────────────────────────────── */

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div
      style={{
        padding: "16px 20px",
        background: "var(--bg-surface)",
        border: "1px solid var(--border)",
        borderRadius: 8,
      }}
    >
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text)" }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-dim)", marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

/* ── Mini bar chart ─────────────────────────────────────────────────────── */

function BarChart({
  data,
  valueKey,
  color,
  label,
}: {
  data: DailyStats[];
  valueKey: keyof DailyStats;
  color: string;
  label: string;
}) {
  const values = data.map((d) => (d[valueKey] as number) ?? 0);
  const max = Math.max(...values, 1);

  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "16px 20px",
      }}
    >
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase", marginBottom: 12 }}>
        {label}
      </div>
      <div className="flex items-end gap-1" style={{ height: 80 }}>
        {data.map((d, i) => {
          const v = (d[valueKey] as number) ?? 0;
          const pct = (v / max) * 100;
          return (
            <div
              key={d.date}
              title={`${d.date}: ${valueKey === "cost_usd" ? formatCost(v) : v.toLocaleString()}`}
              style={{
                flex: 1,
                height: `${Math.max(pct, 2)}%`,
                background: color,
                borderRadius: "2px 2px 0 0",
                minWidth: 3,
                opacity: i === data.length - 1 ? 1 : 0.7,
              }}
            />
          );
        })}
      </div>
      <div className="flex justify-between" style={{ marginTop: 6, fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>
        <span>{data.length > 0 ? data[0].date.slice(5) : ""}</span>
        <span>{data.length > 0 ? data[data.length - 1].date.slice(5) : ""}</span>
      </div>
    </div>
  );
}

/* ── Ranking table ──────────────────────────────────────────────────────── */

function RankingTable({
  title,
  items,
  nameKey,
  formatName,
}: {
  title: string;
  items: Array<{ [key: string]: unknown }>;
  nameKey: string;
  formatName?: (v: string) => string;
}) {
  if (items.length === 0) return null;
  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", color: "var(--text-dim)", textTransform: "uppercase" }}>
          {title}
        </span>
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            <th style={{ padding: "8px 16px", textAlign: "left", fontSize: 10, fontWeight: 600, color: "var(--text-dim)", letterSpacing: "0.06em" }}>NAME</th>
            <th style={{ padding: "8px 16px", textAlign: "right", fontSize: 10, fontWeight: 600, color: "var(--text-dim)", letterSpacing: "0.06em" }}>RUNS</th>
            <th style={{ padding: "8px 16px", textAlign: "right", fontSize: 10, fontWeight: 600, color: "var(--text-dim)", letterSpacing: "0.06em" }}>TOKENS</th>
            <th style={{ padding: "8px 16px", textAlign: "right", fontSize: 10, fontWeight: 600, color: "var(--text-dim)", letterSpacing: "0.06em" }}>COST</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => {
            const name = String(item[nameKey] ?? "");
            return (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 16px", fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                  {formatName ? formatName(name) : name}
                </td>
                <td style={{ padding: "8px 16px", textAlign: "right", fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                  {(item.runs as number).toLocaleString()}
                </td>
                <td style={{ padding: "8px 16px", textAlign: "right", fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                  {formatTokens(item.tokens as number)}
                </td>
                <td style={{ padding: "8px 16px", textAlign: "right", fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                  {formatCost(item.cost_usd as number)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function AnalyticsPage() {
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState(30);

  const [agents, setAgents] = useState<AgentStats[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [apiStatus, setApiStatus] = useState<ApiStatus | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const fetchChannels = useCallback(() => {
    listChannels().then(setChannels).catch(() => {});
  }, []);

  useEffect(() => {
    listAgents({ limit: 100 }).then((d) => setAgents(d.items ?? [])).catch(() => {});
    checkApiStatus().then(setApiStatus);
    fetchChannels();
  }, [fetchChannels]);

  useEffect(() => {
    setLoading(true);
    getAnalytics(period)
      .then(setAnalytics)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [period]);

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
        <header style={{ padding: "20px 32px", borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center justify-between">
            <h1 style={{ fontSize: 16, fontWeight: 600, color: "var(--text)", margin: 0 }}>Analytics</h1>
            <div className="flex items-center gap-1">
              {[7, 14, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setPeriod(d)}
                  className="cursor-pointer"
                  style={{
                    padding: "4px 12px", borderRadius: 6, border: "none",
                    fontSize: 12, fontWeight: period === d ? 500 : 400,
                    background: period === d ? "var(--bg-raised)" : "transparent",
                    color: period === d ? "var(--text)" : "var(--text-muted)",
                  }}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>
        </header>

        {loading && (
          <div style={{ padding: 32 }} className="flex flex-col gap-4">
            <div className="flex gap-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ flex: 1, height: 90, borderRadius: 8 }} />
              ))}
            </div>
            <div className="flex gap-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ flex: 1, height: 150, borderRadius: 8 }} />
              ))}
            </div>
          </div>
        )}

        {!loading && analytics && (
          <div style={{ padding: "24px 32px" }} className="flex flex-col gap-6">
            {/* Summary cards */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 16 }}>
              <StatCard label="Total Runs" value={analytics.total_runs.toLocaleString()} />
              <StatCard label="Total Tokens" value={formatTokens(analytics.total_tokens)} />
              <StatCard label="Total Cost" value={formatCost(analytics.total_cost_usd)} />
              <StatCard
                label="Avg Duration"
                value={analytics.avg_duration_ms != null ? formatDuration(analytics.avg_duration_ms) : "--"}
              />
              <StatCard
                label="Error Rate"
                value={`${(analytics.error_rate * 100).toFixed(1)}%`}
                sub={`${Math.round(analytics.error_rate * analytics.total_runs)} errors`}
              />
            </div>

            {/* Charts */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
              <BarChart data={analytics.days} valueKey="runs" color="var(--purple)" label="Runs / Day" />
              <BarChart data={analytics.days} valueKey="cost_usd" color="var(--green)" label="Cost / Day" />
              <BarChart data={analytics.days} valueKey="tokens" color="var(--blue)" label="Tokens / Day" />
            </div>

            {/* Tables */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <RankingTable title="Top Agents" items={analytics.top_agents} nameKey="agent_id" />
              <RankingTable title="Top Models" items={analytics.top_models} nameKey="model" formatName={shortModel} />
            </div>
          </div>
        )}

        {!loading && !analytics && (
          <div className="flex items-center justify-center" style={{ padding: 64, color: "var(--text-muted)", fontSize: 13 }}>
            No analytics data available.
          </div>
        )}
      </main>
    </div>
  );
}
