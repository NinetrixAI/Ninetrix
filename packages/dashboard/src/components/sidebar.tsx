"use client";

import { useState, useMemo } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import type { AgentStats, ApiStatus, Channel } from "@/lib/api";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/dashboard";
import { normalizeStatus } from "@/lib/utils";
import ThemeToggle from "@/components/theme-toggle";
import { CreateChannelDialog, ManageChannelDialog } from "@/components/channel-dialog";

const NAV_ITEMS = [
  { label: "Runs",   href: "/runs",   icon: RunsIcon },
  { label: "Agents", href: "/agents", icon: AgentsIcon },
];

function RunsIcon({ active }: { active: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <rect x="2" y="3" width="12" height="2" rx="1" fill={active ? "#A78BFA" : "currentColor"} opacity={active ? 1 : 0.5} />
      <rect x="2" y="7" width="9" height="2" rx="1" fill={active ? "#A78BFA" : "currentColor"} opacity={active ? 0.7 : 0.35} />
      <rect x="2" y="11" width="11" height="2" rx="1" fill={active ? "#A78BFA" : "currentColor"} opacity={active ? 0.5 : 0.25} />
    </svg>
  );
}

function AgentsIcon({ active }: { active: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="5" r="3" stroke={active ? "#A78BFA" : "currentColor"} strokeWidth="1.5" fill="none" opacity={active ? 1 : 0.5} />
      <path d="M3 14c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke={active ? "#A78BFA" : "currentColor"} strokeWidth="1.5" fill="none" strokeLinecap="round" opacity={active ? 0.7 : 0.35} />
    </svg>
  );
}

/* ── New Agent Dialog ───────────────────────────────────────────────────── */

function NewAgentDialog({ onClose }: { onClose: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);

  function copy(text: string, id: string) {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  }

  const steps = [
    {
      id: "init",
      label: "1. Create a new agent",
      cmd: "ninetrix init --name my-agent --provider anthropic --yes",
      desc: "Scaffolds agentfile.yaml with sensible defaults",
    },
    {
      id: "build",
      label: "2. Build the container",
      cmd: "ninetrix build",
      desc: "Packages your agent into an isolated Docker image",
    },
    {
      id: "run",
      label: "3. Run it",
      cmd: "ninetrix run",
      desc: "Starts the agent and streams telemetry to this dashboard",
    },
  ];

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
          maxWidth: 480,
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
            padding: "18px 24px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 650, color: "var(--text)", letterSpacing: "-0.02em" }}>
              New Agent
            </h2>
            <p style={{ margin: "3px 0 0", fontSize: 12.5, color: "var(--text-muted)" }}>
              Create and run an agent from your terminal
            </p>
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

        {/* Steps */}
        <div style={{ padding: "20px 24px 24px" }}>
          <div className="flex flex-col" style={{ gap: 16 }}>
            {steps.map((step) => (
              <div key={step.id}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
                  {step.label}
                </div>
                <p style={{ margin: "0 0 8px", fontSize: 11.5, color: "var(--text-muted)", lineHeight: 1.4 }}>
                  {step.desc}
                </p>
                <div
                  className="flex items-center justify-between"
                  style={{
                    padding: "10px 12px",
                    borderRadius: 8,
                    background: "var(--code-bg)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <code
                    style={{
                      fontSize: 12,
                      fontFamily: "var(--font-mono)",
                      color: "var(--purple)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    $ {step.cmd}
                  </code>
                  <button
                    onClick={() => copy(step.cmd, step.id)}
                    className="flex items-center justify-center cursor-pointer shrink-0 ml-2 transition-colors"
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: 5,
                      border: "1px solid var(--border)",
                      background: copied === step.id ? "var(--purple-dim)" : "transparent",
                      color: copied === step.id ? "var(--purple)" : "var(--text-muted)",
                    }}
                    title="Copy"
                  >
                    {copied === step.id ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>

        </div>
      </div>
    </>
  );
}

/* ── Sidebar ────────────────────────────────────────────────────────────── */

interface SidebarProps {
  agents: AgentStats[];
  channels: Channel[];
  apiStatus: ApiStatus | null;
  autoRefresh: boolean;
  onToggleAutoRefresh: () => void;
  onChannelsChanged: () => void;
}

export default function Sidebar({ agents, channels, apiStatus, autoRefresh, onToggleAutoRefresh, onChannelsChanged }: SidebarProps) {
  const pathname = usePathname();
  const [showNewAgent, setShowNewAgent] = useState(false);
  const [showCreateChannel, setShowCreateChannel] = useState(false);
  const [manageChannelId, setManageChannelId] = useState<string | null>(null);

  const activeAgents = useMemo(() => {
    return agents.filter((a) => {
      const norm = normalizeStatus(a.last_status);
      return norm === "running" || norm === "idle";
    });
  }, [agents]);

  return (
    <>
      <aside
        className="fixed top-0 left-0 bottom-0 flex flex-col"
        style={{
          width: "var(--sidebar-w)",
          background: "var(--bg-sidebar)",
          borderRight: "1px solid var(--border)",
          zIndex: 50,
        }}
      >
        {/* Logo */}
        <div
          className="flex items-center gap-2.5 shrink-0"
          style={{ padding: "20px 20px 16px" }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`${BASE_PATH}/ninetrix-logo.png`}
            alt="Ninetrix"
            width={28}
            height={28}
            className="shrink-0"
            style={{ borderRadius: 7, display: "block" }}
          />
          <span
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: "var(--text)",
              letterSpacing: "-0.02em",
            }}
          >
            ninetrix
          </span>
        </div>

        {/* Divider */}
        <div style={{ height: 1, background: "var(--border)", margin: "0 16px" }} />

        {/* Navigation */}
        <nav className="flex flex-col gap-0.5" style={{ padding: "12px 12px" }}>
          {NAV_ITEMS.map((item) => {
            const fullHref = `${BASE_PATH}${item.href}`;
            const active = pathname === fullHref || pathname.startsWith(fullHref + "/");
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="flex items-center gap-2.5 no-underline transition-colors"
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  fontSize: 13.5,
                  fontWeight: active ? 500 : 400,
                  color: active ? "var(--purple)" : "var(--text-muted)",
                  background: active ? "var(--bg-active)" : "transparent",
                  letterSpacing: "-0.01em",
                }}
              >
                <Icon active={active} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Channels */}
        <div style={{ padding: "0 16px 8px" }}>
          <div style={{ height: 1, background: "var(--border)", marginBottom: 12 }} />
          <div
            className="flex items-center justify-between"
            style={{ marginBottom: 8, paddingLeft: 4, paddingRight: 2 }}
          >
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: "var(--text-muted)",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
              }}
            >
              Channels
            </span>
            <button
              onClick={() => setShowCreateChannel(true)}
              className="flex items-center gap-1 cursor-pointer transition-colors"
              style={{
                padding: "2px 7px",
                borderRadius: 5,
                border: "1px solid var(--border)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: 10.5,
                fontWeight: 500,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--blue-dim)"; e.currentTarget.style.color = "var(--blue)"; e.currentTarget.style.borderColor = "rgba(96,165,250,0.25)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Connect
            </button>
          </div>
          {channels.length > 0 ? (
            <div className="flex flex-col gap-1">
              {channels.slice(0, 6).map((ch) => (
                <button
                  key={ch.id}
                  onClick={() => setManageChannelId(ch.id)}
                  className="flex items-center justify-between w-full cursor-pointer transition-colors"
                  style={{
                    padding: "4px 4px",
                    fontSize: 12,
                    background: "transparent",
                    border: "none",
                    borderRadius: 4,
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <div className="flex items-center gap-2" style={{ minWidth: 0 }}>
                    <span
                      className="shrink-0 rounded-full"
                      style={{
                        width: 5,
                        height: 5,
                        background: ch.verified && ch.enabled ? "var(--green)" : ch.verified ? "var(--text-dim)" : "var(--amber)",
                        animation: ch.verified && ch.enabled ? "pulse-dot 2s ease-in-out infinite" : "none",
                      }}
                    />
                    <span
                      className="truncate"
                      style={{
                        color: "var(--text-secondary)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        letterSpacing: "-0.01em",
                      }}
                    >
                      {ch.name}
                    </span>
                  </div>
                  <span
                    style={{
                      color: "var(--text-dim)",
                      fontSize: 10,
                      flexShrink: 0,
                      marginLeft: 6,
                      textTransform: "capitalize",
                    }}
                  >
                    {ch.channel_type}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div
              style={{
                padding: "6px 4px",
                fontSize: 11.5,
                color: "var(--text-dim)",
                fontFamily: "var(--font-mono)",
              }}
            >
              No channels
            </div>
          )}
        </div>

        {/* Active agents */}
        <div style={{ padding: "0 16px 12px" }}>
          <div style={{ height: 1, background: "var(--border)", marginBottom: 12 }} />
          <div
            className="flex items-center justify-between"
            style={{ marginBottom: 10, paddingLeft: 4, paddingRight: 2 }}
          >
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: "var(--text-muted)",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
              }}
            >
              Agents
            </span>
            <button
              onClick={() => setShowNewAgent(true)}
              className="flex items-center gap-1 cursor-pointer transition-colors"
              style={{
                padding: "2px 7px",
                borderRadius: 5,
                border: "1px solid var(--border)",
                background: "transparent",
                color: "var(--text-muted)",
                fontSize: 10.5,
                fontWeight: 500,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--purple-dim)"; e.currentTarget.style.color = "var(--purple)"; e.currentTarget.style.borderColor = "rgba(167,139,250,0.25)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
              New
            </button>
          </div>
          {activeAgents.length > 0 ? (
            <div className="flex flex-col gap-1">
              {activeAgents.slice(0, 8).map((agent) => {
                const norm = normalizeStatus(agent.last_status);
                const isAlive = agent.last_heartbeat != null &&
                  (Date.now() - new Date(agent.last_heartbeat).getTime()) < 45_000;
                const isRunningOrIdle = norm === "running" || norm === "idle";

                const dotColor =
                  isAlive && isRunningOrIdle ? "var(--green)" :
                  isRunningOrIdle && !isAlive ? "var(--amber)" :
                  "var(--green)";

                const shouldPulse = isAlive && isRunningOrIdle;

                const tooltip = isAlive ? "Live" : "Unresponsive";

                return (
                  <div
                    key={agent.agent_id}
                    className="flex items-center justify-between"
                    style={{ padding: "4px 4px", fontSize: 12.5 }}
                    title={tooltip}
                  >
                    <div className="flex items-center gap-2" style={{ minWidth: 0 }}>
                      <span
                        className="shrink-0 rounded-full"
                        style={{
                          width: 5,
                          height: 5,
                          background: dotColor,
                          animation: shouldPulse ? "pulse-dot 2s ease-in-out infinite" : "none",
                        }}
                      />
                      <span
                        className="truncate"
                        style={{
                          color: "var(--text-secondary)",
                          fontFamily: "var(--font-mono)",
                          fontSize: 12,
                          letterSpacing: "-0.01em",
                        }}
                      >
                        {agent.agent_id}
                      </span>
                    </div>
                    <span
                      style={{
                        color: "var(--text-dim)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        marginLeft: 8,
                        flexShrink: 0,
                      }}
                    >
                      {agent.running_runs}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div
              style={{
                padding: "8px 4px",
                fontSize: 11.5,
                color: "var(--text-dim)",
                fontFamily: "var(--font-mono)",
              }}
            >
              No active agents
            </div>
          )}
        </div>

        {/* Bottom controls */}
        <div style={{ borderTop: "1px solid var(--border)", padding: "12px 16px" }}>
          {/* Live toggle */}
          <button
            onClick={onToggleAutoRefresh}
            className="flex items-center gap-2 w-full cursor-pointer transition-colors"
            style={{
              padding: "6px 8px",
              borderRadius: 6,
              border: "none",
              background: autoRefresh ? "var(--purple-dim)" : "transparent",
              color: autoRefresh ? "var(--purple)" : "var(--text-muted)",
              fontSize: 12,
              fontWeight: 500,
              fontFamily: "var(--font-mono)",
            }}
          >
            <span
              className="rounded-full shrink-0"
              style={{
                width: 6,
                height: 6,
                background: autoRefresh ? "var(--purple)" : "var(--text-dim)",
                animation: autoRefresh ? "pulse-dot 2s ease-in-out infinite" : "none",
              }}
            />
            {autoRefresh ? "Live" : "Paused"}
          </button>

          {/* API status + theme toggle */}
          <div
            className="flex items-center justify-between mt-2"
            style={{ padding: "4px 8px" }}
          >
            <div
              className="flex items-center gap-2"
              style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
            >
              <span
                className="rounded-full shrink-0"
                style={{
                  width: 5,
                  height: 5,
                  background: apiStatus == null
                    ? "var(--text-dim)"
                    : apiStatus.connected
                    ? "var(--green)"
                    : "var(--red)",
                }}
              />
              <span style={{ color: "var(--text-dim)" }}>
                {apiStatus == null
                  ? "connecting..."
                  : apiStatus.connected
                  ? `API ${apiStatus.latencyMs}ms`
                  : "disconnected"}
              </span>
            </div>
            <ThemeToggle />
          </div>
        </div>
      </aside>

      {showNewAgent && <NewAgentDialog onClose={() => setShowNewAgent(false)} />}
      {showCreateChannel && (
        <CreateChannelDialog
          onClose={() => setShowCreateChannel(false)}
          onCreated={onChannelsChanged}
        />
      )}
      {manageChannelId && (
        <ManageChannelDialog
          channelId={manageChannelId}
          agents={agents}
          onClose={() => setManageChannelId(null)}
          onChanged={onChannelsChanged}
        />
      )}
    </>
  );
}
