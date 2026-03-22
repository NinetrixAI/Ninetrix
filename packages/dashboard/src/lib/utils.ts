export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "--";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function formatRelTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 5000) return "just now";
  if (diff < 60000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  const d = new Date(iso);
  const now = new Date();
  if (d.getFullYear() === now.getFullYear())
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  } as Intl.DateTimeFormatOptions);
}

export function shortModel(model: string): string {
  if (!model) return "";
  const map: Record<string, string> = {
    "claude-opus": "Opus",
    "claude-sonnet": "Sonnet",
    "claude-haiku": "Haiku",
    "gpt-4o": "GPT-4o",
    "gpt-4": "GPT-4",
    "gpt-3.5": "GPT-3.5",
    "gemini": "Gemini",
    "llama": "Llama",
  };
  for (const [key, val] of Object.entries(map)) {
    if (model.toLowerCase().includes(key)) return val;
  }
  return model.split("-").slice(-1)[0] || model;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.001) return `$${usd.toFixed(4)}`;
  if (usd < 0.01) return `$${usd.toFixed(3)}`;
  if (usd < 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(2)}`;
}

export function normalizeStatus(
  s: string,
): "running" | "completed" | "idle" | "error" | "pending" | "cancelled" | "budget" {
  if (s === "in_progress" || s === "started" || s === "running") return "running";
  if (s === "completed" || s === "approved") return "completed";
  if (s === "idle") return "idle";
  if (s === "error" || s === "failed") return "error";
  if (s === "waiting_for_approval" || s === "pending") return "pending";
  if (s === "budget_exceeded") return "budget";
  return "cancelled";
}
