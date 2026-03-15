"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console for debugging; replace with a real logger if needed
    console.error("[dashboard] unhandled error:", error);
  }, [error]);

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--bg-base)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div style={{ textAlign: "center", maxWidth: 400, padding: "0 24px" }}>
        <div
          style={{
            fontSize: 32,
            color: "#EF4444",
            marginBottom: 16,
            fontFamily: "var(--font-jb-mono, monospace)",
          }}
        >
          ✕
        </div>
        <h2
          style={{
            margin: "0 0 8px",
            fontSize: 18,
            fontWeight: 700,
            color: "var(--text-primary)",
            fontFamily: "var(--font-syne, sans-serif)",
          }}
        >
          Something went wrong
        </h2>
        <p
          style={{
            margin: "0 0 20px",
            fontSize: 13,
            color: "var(--text-muted)",
            lineHeight: 1.6,
            fontFamily: "var(--font-jb-mono, monospace)",
          }}
        >
          {error.message || "An unexpected error occurred."}
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
          <button
            onClick={reset}
            style={{
              padding: "7px 16px",
              borderRadius: 6,
              border: "1px solid rgba(59,130,246,0.3)",
              background: "var(--accent-blue-dim)",
              color: "var(--accent-blue)",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
          <a
            href="/dashboard/threads"
            style={{
              padding: "7px 16px",
              borderRadius: 6,
              border: "1px solid var(--border-strong)",
              background: "transparent",
              color: "var(--text-muted)",
              fontSize: 13,
              fontWeight: 500,
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
            }}
          >
            ← Back to Threads
          </a>
        </div>
      </div>
    </div>
  );
}
