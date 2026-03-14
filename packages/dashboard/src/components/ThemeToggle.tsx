"use client";

import { useState, useEffect } from "react";

type Theme = "dark" | "light";

function SunIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4" />
      <line x1="12" y1="20" x2="12" y2="22" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="2" y1="12" x2="4" y2="12" />
      <line x1="20" y1="12" x2="22" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" stroke="none">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const saved = localStorage.getItem("nxt-theme") as Theme | null;
    const initial = saved ?? "dark";
    setTheme(initial);
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("nxt-theme", next);
    if (next === "light") {
      document.documentElement.classList.add("light");
    } else {
      document.documentElement.classList.remove("light");
    }
  };

  if (!mounted) {
    // Render a placeholder to avoid layout shift
    return (
      <div
        style={{
          width: 52,
          height: 26,
          borderRadius: 13,
          background: "var(--toggle-track)",
          border: "1px solid var(--border-strong)",
        }}
      />
    );
  }

  const isDark = theme === "dark";

  return (
    <button
      onClick={toggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Light mode" : "Dark mode"}
      style={{
        position: "relative",
        width: 52,
        height: 26,
        borderRadius: 13,
        border: "1px solid var(--border-strong)",
        background: isDark
          ? "rgba(148, 163, 184, 0.08)"
          : "rgba(59, 130, 246, 0.12)",
        cursor: "pointer",
        padding: 0,
        flexShrink: 0,
        transition: "background 0.25s ease, border-color 0.25s ease",
        outline: "none",
        display: "flex",
        alignItems: "center",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor =
          "var(--border-strong)";
        (e.currentTarget as HTMLButtonElement).style.background = isDark
          ? "rgba(148, 163, 184, 0.13)"
          : "rgba(59, 130, 246, 0.18)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = isDark
          ? "rgba(148, 163, 184, 0.08)"
          : "rgba(59, 130, 246, 0.12)";
      }}
    >
      {/* Track icons (always visible, faint) */}
      <span
        style={{
          position: "absolute",
          left: 7,
          top: "50%",
          transform: "translateY(-50%)",
          color: isDark ? "var(--text-dim)" : "var(--accent-blue)",
          opacity: isDark ? 0.3 : 0.7,
          transition: "opacity 0.2s, color 0.2s",
          lineHeight: 1,
          pointerEvents: "none",
        }}
      >
        <SunIcon />
      </span>
      <span
        style={{
          position: "absolute",
          right: 7,
          top: "50%",
          transform: "translateY(-50%)",
          color: isDark ? "var(--text-secondary)" : "var(--text-dim)",
          opacity: isDark ? 0.6 : 0.25,
          transition: "opacity 0.2s, color 0.2s",
          lineHeight: 1,
          pointerEvents: "none",
        }}
      >
        <MoonIcon />
      </span>

      {/* Sliding thumb */}
      <span
        style={{
          position: "absolute",
          top: 3,
          left: isDark ? "calc(100% - 23px - 3px)" : 3,
          width: 20,
          height: 20,
          borderRadius: "50%",
          background: isDark
            ? "linear-gradient(135deg, #1E293B, #334155)"
            : "linear-gradient(135deg, #FFFFFF, #F1F5F9)",
          boxShadow: isDark
            ? "0 1px 4px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.06)"
            : "0 1px 4px rgba(15,23,42,0.15), inset 0 1px 0 rgba(255,255,255,0.9)",
          transition: "left 0.22s cubic-bezier(0.34, 1.56, 0.64, 1), background 0.22s ease, box-shadow 0.22s ease",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          pointerEvents: "none",
        }}
      >
        {/* Icon inside thumb */}
        <span
          key={theme} // re-mounts on change → triggers animation
          className="animate-theme-icon"
          style={{
            color: isDark ? "#64748B" : "#F59E0B",
            lineHeight: 1,
          }}
        >
          {isDark ? <MoonIcon /> : <SunIcon />}
        </span>
      </span>
    </button>
  );
}
