export default function NotFound() {
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
      <div style={{ textAlign: "center", maxWidth: 360, padding: "0 24px" }}>
        <div
          style={{
            fontSize: 48,
            fontWeight: 800,
            fontFamily: "var(--font-jb-mono, monospace)",
            color: "var(--text-dim)",
            letterSpacing: "-0.04em",
            marginBottom: 12,
          }}
        >
          404
        </div>
        <h2
          style={{
            margin: "0 0 8px",
            fontSize: 17,
            fontWeight: 700,
            color: "var(--text-primary)",
            fontFamily: "var(--font-syne, sans-serif)",
          }}
        >
          Page not found
        </h2>
        <p
          style={{
            margin: "0 0 24px",
            fontSize: 13,
            color: "var(--text-muted)",
            lineHeight: 1.6,
          }}
        >
          This page doesn&apos;t exist or has been moved.
        </p>
        <a
          href="/dashboard/threads"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "7px 16px",
            borderRadius: 6,
            border: "1px solid var(--border-strong)",
            background: "transparent",
            color: "var(--text-secondary)",
            fontSize: 13,
            fontWeight: 500,
            textDecoration: "none",
            transition: "background 0.12s, color 0.12s",
          }}
        >
          ← Back to Threads
        </a>
      </div>
    </div>
  );
}
