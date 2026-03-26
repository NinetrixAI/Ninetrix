"use client";

import { useState, useEffect, useRef } from "react";
import type { Channel, ChannelBinding, AgentStats } from "@/lib/api";
import {
  createChannel,
  getChannel,
  listChannels,
  updateChannel,
  deleteChannel,
  verifyChannel,
  bindAgent,
  unbindAgent,
} from "@/lib/api";
import { formatRelTime } from "@/lib/utils";

/* ── Icons ──────────────────────────────────────────────────────────────── */

function TelegramIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DiscordIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function WhatsAppIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PlatformIcon({ type, size = 16 }: { type: string; size?: number }) {
  switch (type) {
    case "telegram": return <TelegramIcon size={size} />;
    case "discord": return <DiscordIcon size={size} />;
    case "whatsapp": return <WhatsAppIcon size={size} />;
    default: return <TelegramIcon size={size} />;
  }
}

/* ── Platform definitions ──────────────────────────────────────────────── */

interface PlatformDef {
  id: string;
  label: string;
  description: string;
  color: string;
  colorDim: string;
  tokenLabel: string;
  tokenHint: string;
  tokenPlaceholder: string;
  tokenIsSecret: boolean;       // true = password input with toggle, false = plain text
  nameLabel: string;
  namePlaceholder: string;
  needsVerification: boolean;
  needsPostConnect: boolean;    // true = show post-connect steps (invite URL, etc.)
  usesCli: boolean;             // true = QR/CLI pairing, no form — show CLI instructions
  cliCommand?: string;          // CLI command to run for pairing
  setupSteps: string[];         // step-by-step instructions shown above the token field
  verifyInstructions?: (botUsername: string) => string;
}

const PLATFORMS: PlatformDef[] = [
  {
    id: "telegram",
    label: "Telegram",
    description: "Connect a Telegram bot",
    color: "var(--blue)",
    colorDim: "var(--blue-dim)",
    tokenLabel: "Bot token",
    tokenHint: "Get this from @BotFather on Telegram",
    tokenPlaceholder: "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ",
    tokenIsSecret: true,
    nameLabel: "Bot name",
    namePlaceholder: "e.g. Support Bot",
    needsVerification: true,
    needsPostConnect: false,
    usesCli: false,
    setupSteps: [
      "Open Telegram and search for @BotFather",
      "Send /newbot and pick a name + username",
      "Copy the bot token BotFather gives you",
    ],
    verifyInstructions: (bot) => `Send /start to @${bot}, then send the verification code`,
  },
  {
    id: "discord",
    label: "Discord",
    description: "Connect a Discord bot",
    color: "var(--purple)",
    colorDim: "var(--purple-dim)",
    tokenLabel: "Bot token",
    tokenHint: "Copy the token from the Bot tab",
    tokenPlaceholder: "MTIz...your-bot-token",
    tokenIsSecret: true,
    nameLabel: "Bot name",
    namePlaceholder: "e.g. My Agent",
    needsVerification: false,
    needsPostConnect: true,
    usesCli: false,
    setupSteps: [
      "Go to discord.com/developers/applications",
      "Click New Application → name it → create",
      "Go to Bot tab → click Reset Token → copy it",
      "Enable Message Content Intent under Privileged Gateway Intents",
    ],
  },
  {
    id: "whatsapp",
    label: "WhatsApp",
    description: "Connect via WhatsApp Web",
    color: "var(--green)",
    colorDim: "rgba(74,222,128,0.12)",
    tokenLabel: "Phone number",
    tokenHint: "Use a dedicated number (eSIM recommended) — not your personal WhatsApp",
    tokenPlaceholder: "+1234567890",
    tokenIsSecret: false,
    nameLabel: "Display name",
    namePlaceholder: "e.g. Agent WhatsApp",
    needsVerification: false,
    needsPostConnect: false,
    usesCli: true,
    setupSteps: [],
    cliCommand: "ninetrix channel connect whatsapp",
  },
];

/* ── Create Dialog ──────────────────────────────────────────────────────── */

interface CreateProps {
  onClose: () => void;
  onCreated: () => void;
}

function CreateChannelDialog({ onClose, onCreated }: CreateProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [platform, setPlatform] = useState<PlatformDef | null>(null);
  const [botName, setBotName] = useState("");
  const [botToken, setBotToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [channel, setChannel] = useState<Channel | null>(null);
  const [verified, setVerified] = useState(false);
  const [copied, setCopied] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function selectPlatform(p: PlatformDef) {
    setPlatform(p);
    if (p.usesCli) {
      // CLI-paired channels skip the form — go straight to "waiting" step
      setStep(3);
    } else {
      setStep(2);
    }
  }

  async function handleCreate() {
    if (!platform || !botName.trim() || !botToken.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const config: Record<string, string> = { bot_token: botToken.trim() };
      const ch = await createChannel(platform.id, botName.trim(), config);

      // Auto-verify for non-Telegram channels (Discord, WhatsApp)
      // Telegram uses the 6-digit code flow instead.
      if (!platform.needsVerification) {
        const code = ch.config?.verification_code || "000000";
        await verifyChannel(ch.id, code).catch(() => {});
        ch.verified = true;
      }

      setChannel(ch);
      if (platform.needsVerification) {
        // Telegram: go to verification step
        setStep(3);
      } else if (platform.needsPostConnect) {
        // Discord: go to post-connect step (invite URL + Message Content Intent)
        setStep(3);
      } else {
        setVerified(true);
        setStep(3);
        onCreated();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create channel");
    } finally {
      setCreating(false);
    }
  }

  // Poll for verification (Telegram) or CLI pairing (WhatsApp) completion
  useEffect(() => {
    if (step !== 3 || verified) return;

    // For CLI-paired platforms: poll the channel list for a new whatsapp channel
    if (platform?.usesCli) {
      pollRef.current = setInterval(async () => {
        try {
          const channels = await listChannels();
          const found = channels.find(
            (ch: Channel) => ch.channel_type === platform.id && ch.verified
          );
          if (found) {
            setChannel(found);
            setVerified(true);
            if (pollRef.current) clearInterval(pollRef.current);
            onCreated();
          }
        } catch { /* ignore */ }
      }, 3000);
      return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }

    // For Telegram: poll the specific channel for verification
    if (!channel || !platform?.needsVerification) return;
    pollRef.current = setInterval(async () => {
      try {
        const updated = await getChannel(channel.id);
        if (updated.verified) {
          setVerified(true);
          if (pollRef.current) clearInterval(pollRef.current);
          onCreated();
        }
      } catch { /* ignore */ }
    }, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [step, channel, onCreated, platform, verified]);

  function copyCode() {
    const code = channel?.config?.verification_code;
    if (code) {
      navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  const pLabel = platform?.label || "Channel";

  return (
    <>
      <Backdrop onClose={onClose} />
      <DialogShell>
        <DialogHeader
          title={step === 1 ? "Connect Channel" : step === 2 ? `Configure ${pLabel}` : verified ? "Connected!" : "Verify Channel"}
          subtitle={
            step === 1
              ? "Choose a messaging platform"
              : step === 2
              ? `Enter your ${pLabel} details`
              : verified
              ? `${pLabel} is ready`
              : "Verify ownership"
          }
          onClose={onClose}
        />

        <div style={{ padding: "16px 20px 20px" }}>
          {/* Step 1 — Platform selection */}
          {step === 1 && (
            <div className="flex flex-col" style={{ gap: 8 }}>
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => selectPlatform(p)}
                  className="flex items-center gap-3 w-full cursor-pointer transition-colors"
                  style={{
                    padding: "14px 16px",
                    borderRadius: 8,
                    border: "1px solid var(--border-strong)",
                    background: "var(--bg-surface)",
                    color: "var(--text)",
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-raised)"; e.currentTarget.style.borderColor = p.color; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "var(--bg-surface)"; e.currentTarget.style.borderColor = "var(--border-strong)"; }}
                >
                  <div
                    className="flex items-center justify-center shrink-0"
                    style={{ width: 36, height: 36, borderRadius: 8, background: p.colorDim, color: p.color }}
                  >
                    <PlatformIcon type={p.id} size={18} />
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 550 }}>{p.label}</div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 1 }}>{p.description}</div>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Step 2 — Configure */}
          {step === 2 && platform && (
            <div className="flex flex-col" style={{ gap: 14 }}>
              {/* Setup instructions */}
              {platform.setupSteps.length > 0 && (
                <div style={{
                  padding: "10px 14px",
                  borderRadius: 8,
                  background: "var(--bg-surface)",
                  border: "1px solid var(--border)",
                }}>
                  <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                    Setup steps
                  </div>
                  {platform.setupSteps.map((step, i) => (
                    <div key={i} style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
                      {i + 1}. {step}
                    </div>
                  ))}
                </div>
              )}

              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", display: "block", marginBottom: 6 }}>
                  {platform.nameLabel}
                </label>
                <p style={{ margin: "0 0 6px", fontSize: 11.5, color: "var(--text-muted)", lineHeight: 1.4 }}>
                  Unique name to identify this bot (used in agentfile.yaml triggers)
                </p>
                <input
                  type="text"
                  value={botName}
                  onChange={(e) => setBotName(e.target.value)}
                  placeholder={platform.namePlaceholder}
                  className="outline-none w-full"
                  style={{
                    padding: "9px 12px",
                    borderRadius: 7,
                    border: "1px solid var(--border-strong)",
                    background: "var(--bg-surface)",
                    color: "var(--text)",
                    fontSize: 13,
                    fontFamily: "inherit",
                  }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", display: "block", marginBottom: 6 }}>
                  {platform.tokenLabel}
                </label>
                <p style={{ margin: "0 0 6px", fontSize: 11.5, color: "var(--text-muted)", lineHeight: 1.4 }}>
                  {platform.tokenHint}
                </p>
                <div className="relative">
                  <input
                    type={platform.tokenIsSecret && !showToken ? "password" : "text"}
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder={platform.tokenPlaceholder}
                    className="outline-none w-full"
                    style={{
                      padding: platform.tokenIsSecret ? "9px 40px 9px 12px" : "9px 12px",
                      borderRadius: 7,
                      border: "1px solid var(--border-strong)",
                      background: "var(--bg-surface)",
                      color: "var(--text)",
                      fontSize: 13,
                      fontFamily: "var(--font-mono)",
                    }}
                  />
                  {platform.tokenIsSecret && (
                    <button
                      type="button"
                      onClick={() => setShowToken((v) => !v)}
                      className="absolute cursor-pointer"
                      style={{
                        right: 8,
                        top: "50%",
                        transform: "translateY(-50%)",
                        background: "none",
                        border: "none",
                        color: "var(--text-dim)",
                        padding: 4,
                      }}
                      title={showToken ? "Hide" : "Show"}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        {showToken ? (
                          <>
                            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
                            <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                            <line x1="1" y1="1" x2="23" y2="23" />
                          </>
                        ) : (
                          <>
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                            <circle cx="12" cy="12" r="3" />
                          </>
                        )}
                      </svg>
                    </button>
                  )}
                </div>
              </div>

              {error && (
                <div style={{ padding: "8px 12px", borderRadius: 6, background: "var(--red-dim)", color: "var(--red)", fontSize: 12 }}>
                  {error}
                </div>
              )}

              <div className="flex items-center justify-between" style={{ marginTop: 4 }}>
                <button
                  onClick={() => { setStep(1); setPlatform(null); }}
                  className="cursor-pointer transition-colors"
                  style={{
                    padding: "7px 14px",
                    borderRadius: 7,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: "var(--text-muted)",
                    fontSize: 13,
                  }}
                >
                  Back
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating || !botName.trim() || !botToken.trim()}
                  className="cursor-pointer transition-colors"
                  style={{
                    padding: "7px 20px",
                    borderRadius: 7,
                    border: `1px solid color-mix(in srgb, ${platform.color} 30%, transparent)`,
                    background: platform.colorDim,
                    color: platform.color,
                    fontSize: 13,
                    fontWeight: 600,
                    opacity: creating || !botName.trim() || !botToken.trim() ? 0.5 : 1,
                  }}
                >
                  {creating ? "Connecting..." : "Connect"}
                </button>
              </div>
            </div>
          )}

          {/* Step 3 — Verify / Done */}
          {step === 3 && (channel || platform?.usesCli) && (
            <div className="flex flex-col items-center" style={{ gap: 16 }}>
              {platform?.needsPostConnect && channel && !verified ? (
                /* Discord post-connect: invite URL + Message Content Intent */
                <>
                  <div
                    className="flex items-center justify-center"
                    style={{ width: 48, height: 48, borderRadius: 12, background: platform.colorDim, color: platform.color }}
                  >
                    <PlatformIcon type={platform.id} size={24} />
                  </div>

                  <div style={{ textAlign: "center" }}>
                    <p style={{ margin: "0 0 4px", fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
                      Almost there — 2 more steps
                    </p>
                  </div>

                  {/* Step: Message Content Intent */}
                  <div style={{
                    padding: "12px 14px",
                    borderRadius: 8,
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    width: "100%",
                  }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                      1. Enable Message Content Intent
                    </div>
                    <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                      Go to{" "}
                      <a
                        href="https://discord.com/developers/applications"
                        target="_blank"
                        rel="noopener"
                        style={{ color: platform.color, textDecoration: "underline" }}
                      >
                        Discord Developer Portal
                      </a>
                      {" "}→ your app → <strong>Bot</strong> tab → <strong>Privileged Gateway Intents</strong> → enable <strong>Message Content Intent</strong>
                    </p>
                  </div>

                  {/* Step: Invite bot to server */}
                  <div style={{
                    padding: "12px 14px",
                    borderRadius: 8,
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    width: "100%",
                  }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                      2. Add bot to your server
                    </div>
                    <p style={{ margin: "0 0 8px", fontSize: 12, color: "var(--text-muted)" }}>
                      Skip this if the bot is already in your server.
                    </p>
                    <a
                      href={`https://discord.com/oauth2/authorize?client_id=${atob(botToken.split(".")[0])}&permissions=2048&scope=bot`}
                      target="_blank"
                      rel="noopener"
                      className="flex items-center justify-center w-full cursor-pointer"
                      style={{
                        padding: "8px 16px",
                        borderRadius: 7,
                        border: `1px solid color-mix(in srgb, ${platform.color} 30%, transparent)`,
                        background: platform.colorDim,
                        color: platform.color,
                        fontSize: 13,
                        fontWeight: 600,
                        textDecoration: "none",
                      }}
                    >
                      Authorize bot on your server ↗
                    </a>
                  </div>

                  <button
                    onClick={() => { setVerified(true); onCreated(); }}
                    className="cursor-pointer transition-colors w-full"
                    style={{
                      padding: "9px 20px",
                      borderRadius: 7,
                      border: "1px solid rgba(74,222,128,0.3)",
                      background: "rgba(74,222,128,0.12)",
                      color: "var(--green)",
                      fontSize: 13,
                      fontWeight: 600,
                      marginTop: 4,
                    }}
                  >
                    Done
                  </button>
                  <button
                    onClick={onClose}
                    className="cursor-pointer"
                    style={{ background: "none", border: "none", color: "var(--text-dim)", fontSize: 12 }}
                  >
                    I&apos;ll do this later
                  </button>
                </>
              ) : verified ? (
                <>
                  <div
                    className="flex items-center justify-center"
                    style={{ width: 48, height: 48, borderRadius: 12, background: "var(--green-dim)", color: "var(--green)" }}
                  >
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                  <div style={{ textAlign: "center" }}>
                    <p style={{ margin: "0 0 4px", fontSize: 14, fontWeight: 600, color: "var(--green)" }}>
                      {platform?.label || "Channel"} connected!
                    </p>
                    <p style={{ margin: 0, fontSize: 12.5, color: "var(--text-muted)" }}>
                      Messages will now trigger agent runs.
                    </p>
                  </div>
                  <button
                    onClick={onClose}
                    className="cursor-pointer transition-colors w-full"
                    style={{
                      padding: "9px 20px",
                      borderRadius: 7,
                      border: "1px solid rgba(74,222,128,0.3)",
                      background: "rgba(74,222,128,0.12)",
                      color: "var(--green)",
                      fontSize: 13,
                      fontWeight: 600,
                    }}
                  >
                    Done
                  </button>
                </>
              ) : platform?.usesCli ? (
                /* CLI-paired channel (WhatsApp) — show CLI instructions + poll */
                <>
                  <div
                    className="flex items-center justify-center"
                    style={{ width: 48, height: 48, borderRadius: 12, background: platform.colorDim, color: platform.color }}
                  >
                    <PlatformIcon type={platform.id} size={24} />
                  </div>

                  <div style={{ textAlign: "center" }}>
                    <p style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 600, color: "var(--text)" }}>
                      Pair via terminal
                    </p>
                    <p style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--text-muted)", lineHeight: 1.5 }}>
                      {platform.label} requires QR code scanning.<br />
                      Run this command in your terminal:
                    </p>
                  </div>

                  <div
                    className="flex items-center gap-2 w-full"
                    style={{
                      padding: "10px 14px",
                      borderRadius: 8,
                      background: "var(--bg-surface)",
                      border: "1px solid var(--border-strong)",
                    }}
                  >
                    <code style={{ flex: 1, fontSize: 13, fontFamily: "var(--font-mono)", color: platform.color }}>
                      {platform.cliCommand}
                    </code>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(platform.cliCommand || "");
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      }}
                      className="flex items-center justify-center cursor-pointer shrink-0"
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: 6,
                        border: "1px solid var(--border)",
                        background: copied ? "var(--green-dim)" : "transparent",
                        color: copied ? "var(--green)" : "var(--text-muted)",
                      }}
                      title="Copy"
                    >
                      {copied ? (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12" /></svg>
                      ) : (
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
                      )}
                    </button>
                  </div>

                  <div className="flex items-center gap-2" style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
                    <span
                      className="rounded-full shrink-0"
                      style={{
                        width: 6,
                        height: 6,
                        background: platform.color,
                        animation: "pulse-dot 2s ease-in-out infinite",
                      }}
                    />
                    Waiting for pairing... this page will update automatically
                  </div>

                  <button
                    onClick={onClose}
                    className="cursor-pointer"
                    style={{ background: "none", border: "none", color: "var(--text-dim)", fontSize: 12, marginTop: 4 }}
                  >
                    Cancel
                  </button>
                </>
              ) : channel ? (
                /* Telegram verification flow */
                <>
                  <div
                    className="flex items-center justify-center"
                    style={{ width: 48, height: 48, borderRadius: 12, background: platform?.colorDim || "var(--blue-dim)", color: platform?.color || "var(--blue)" }}
                  >
                    <PlatformIcon type={platform?.id || "telegram"} size={24} />
                  </div>

                  <div style={{ textAlign: "center" }}>
                    <p style={{ margin: "0 0 6px", fontSize: 13, color: "var(--text)" }}>
                      Send <code style={{ fontFamily: "var(--font-mono)", color: "var(--purple)", background: "var(--purple-dim)", padding: "1px 5px", borderRadius: 3 }}>/start</code> to{" "}
                      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 500, color: platform?.color || "var(--blue)" }}>
                        @{channel.config.bot_username || "your bot"}
                      </span>
                    </p>
                    <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
                      Then send this code to the bot
                    </p>
                  </div>

                  <div className="flex items-center gap-3" style={{ margin: "4px 0" }}>
                    <div
                      className="flex items-center gap-1"
                      style={{
                        padding: "10px 20px",
                        borderRadius: 10,
                        background: "var(--amber-dim)",
                        border: "1px solid rgba(251,191,36,0.2)",
                      }}
                    >
                      {(channel.config.verification_code || "------").split("").map((digit, i) => (
                        <span
                          key={i}
                          style={{
                            fontSize: 28,
                            fontWeight: 700,
                            fontFamily: "var(--font-mono)",
                            color: "var(--amber)",
                            letterSpacing: "0.08em",
                            width: 24,
                            textAlign: "center",
                          }}
                        >
                          {digit}
                        </span>
                      ))}
                    </div>
                    <button
                      onClick={copyCode}
                      className="flex items-center justify-center cursor-pointer transition-colors shrink-0"
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: 8,
                        border: "1px solid var(--border)",
                        background: copied ? "var(--green-dim)" : "transparent",
                        color: copied ? "var(--green)" : "var(--text-muted)",
                      }}
                      title="Copy code"
                    >
                      {copied ? (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : (
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                        </svg>
                      )}
                    </button>
                  </div>

                  <div className="flex items-center gap-2" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                    <span
                      className="rounded-full shrink-0"
                      style={{
                        width: 6,
                        height: 6,
                        background: "var(--amber)",
                        animation: "pulse-dot 2s ease-in-out infinite",
                      }}
                    />
                    Waiting for verification...
                  </div>

                  <button
                    onClick={onClose}
                    className="cursor-pointer"
                    style={{ background: "none", border: "none", color: "var(--text-dim)", fontSize: 12 }}
                  >
                    I&apos;ll verify later
                  </button>
                </>
              ) : null}
            </div>
          )}
        </div>
      </DialogShell>
    </>
  );
}

/* ── Manage Dialog ──────────────────────────────────────────────────────── */

interface ManageProps {
  channelId: string;
  agents: AgentStats[];
  onClose: () => void;
  onChanged: () => void;
}

function ManageChannelDialog({ channelId, agents, onClose, onChanged }: ManageProps) {
  const [channel, setChannel] = useState<Channel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [bindingAgent, setBindingAgent] = useState(false);
  const [showBindPicker, setShowBindPicker] = useState(false);

  // Fetch channel details with bindings
  useState(() => {
    getChannel(channelId)
      .then((ch) => { setChannel(ch); setLoading(false); })
      .catch((e) => { setError(e instanceof Error ? e.message : "Failed to load"); setLoading(false); });
  });

  async function handleToggleEnabled() {
    if (!channel) return;
    try {
      const updated = await updateChannel(channel.id, { enabled: !channel.enabled });
      setChannel(updated);
      onChanged();
    } catch { /* ignore */ }
  }

  async function handleDelete() {
    if (!channel) return;
    setDeleting(true);
    try {
      await deleteChannel(channel.id);
      onChanged();
      // Small delay so the parent re-fetches channels before dialog closes
      setTimeout(() => onClose(), 200);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete channel");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  async function handleBind(agentName: string) {
    if (!channel) return;
    setBindingAgent(true);
    try {
      await bindAgent(channel.id, agentName);
      const updated = await getChannel(channel.id);
      setChannel(updated);
      setShowBindPicker(false);
      onChanged();
    } catch { /* ignore */ }
    setBindingAgent(false);
  }

  async function handleUnbind(agentName: string) {
    if (!channel) return;
    try {
      await unbindAgent(channel.id, agentName);
      const updated = await getChannel(channel.id);
      setChannel(updated);
      onChanged();
    } catch { /* ignore */ }
  }

  const boundNames = new Set(channel?.agents?.map((a) => a.agent_name) ?? []);
  const availableAgents = agents.filter((a) => !boundNames.has(a.agent_id));

  return (
    <>
      <Backdrop onClose={onClose} />
      <DialogShell>
        {loading ? (
          <div style={{ padding: 32 }}>
            <div className="skeleton" style={{ height: 120, borderRadius: 6 }} />
          </div>
        ) : error ? (
          <div style={{ padding: 20 }}>
            <div style={{ color: "var(--red)", fontSize: 13 }}>{error}</div>
          </div>
        ) : channel ? (
          <>
            {/* Header */}
            <DialogHeader
              title={channel.name}
              subtitle={`${channel.channel_type} \u00b7 ${channel.verified ? "Verified" : "Unverified"} \u00b7 Created ${formatRelTime(channel.created_at)}`}
              onClose={onClose}
            />

            <div style={{ padding: "16px 20px" }}>
              {/* Status row */}
              <div className="flex items-center gap-3 flex-wrap" style={{ marginBottom: 16 }}>
                <span
                  className="inline-flex items-center gap-1.5 rounded-full"
                  style={{
                    padding: "3px 10px",
                    background: channel.verified ? "var(--green-dim)" : "var(--amber-dim)",
                    color: channel.verified ? "var(--green)" : "var(--amber)",
                    fontSize: 12,
                    fontWeight: 500,
                  }}
                >
                  <span
                    className="rounded-full"
                    style={{
                      width: 6,
                      height: 6,
                      background: channel.verified ? "var(--green)" : "var(--amber)",
                    }}
                  />
                  {channel.verified ? "Verified" : "Unverified"}
                </span>
                <span
                  className="inline-flex items-center gap-1.5 rounded-full"
                  style={{
                    padding: "3px 10px",
                    background: channel.enabled ? "var(--blue-dim)" : "var(--bg-raised)",
                    color: channel.enabled ? "var(--blue)" : "var(--text-dim)",
                    fontSize: 12,
                    fontWeight: 500,
                  }}
                >
                  {channel.enabled ? "Enabled" : "Disabled"}
                </span>
                {channel.config.bot_username && (
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    @{channel.config.bot_username}
                  </span>
                )}
              </div>

              {/* Settings */}
              <div className="flex flex-col" style={{ gap: 10, marginBottom: 16 }}>
                <div className="flex items-center justify-between" style={{ fontSize: 12.5 }}>
                  <span style={{ color: "var(--text-muted)" }}>Session mode</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{channel.session_mode}</span>
                </div>
                <div className="flex items-center justify-between" style={{ fontSize: 12.5 }}>
                  <span style={{ color: "var(--text-muted)" }}>Routing mode</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{channel.routing_mode}</span>
                </div>
                <div className="flex items-center justify-between" style={{ fontSize: 12.5 }}>
                  <span style={{ color: "var(--text-muted)" }}>Enabled</span>
                  <button
                    onClick={handleToggleEnabled}
                    className="cursor-pointer"
                    style={{
                      width: 36,
                      height: 20,
                      borderRadius: 10,
                      border: "none",
                      background: channel.enabled ? "var(--green)" : "var(--bg-raised)",
                      position: "relative",
                      transition: "background 0.2s ease",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        top: 2,
                        left: channel.enabled ? 18 : 2,
                        width: 16,
                        height: 16,
                        borderRadius: 8,
                        background: "#fff",
                        transition: "left 0.2s ease",
                      }}
                    />
                  </button>
                </div>
              </div>

              {/* Bound agents */}
              <div style={{ marginBottom: 16 }}>
                <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    Bound Agents
                  </span>
                  {availableAgents.length > 0 && (
                    <button
                      onClick={() => setShowBindPicker((v) => !v)}
                      className="cursor-pointer transition-colors"
                      style={{
                        padding: "2px 7px",
                        borderRadius: 5,
                        border: "1px solid var(--border)",
                        background: "transparent",
                        color: "var(--text-muted)",
                        fontSize: 10.5,
                        fontWeight: 500,
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--purple-dim)"; e.currentTarget.style.color = "var(--purple)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; }}
                    >
                      + Bind
                    </button>
                  )}
                </div>

                {/* Agent picker dropdown */}
                {showBindPicker && (
                  <div
                    style={{
                      marginBottom: 8,
                      padding: "4px",
                      borderRadius: 8,
                      border: "1px solid var(--border-strong)",
                      background: "var(--bg-surface)",
                      maxHeight: 160,
                      overflow: "auto",
                    }}
                  >
                    {availableAgents.map((a) => (
                      <button
                        key={a.agent_id}
                        onClick={() => handleBind(a.agent_id)}
                        disabled={bindingAgent}
                        className="flex items-center gap-2 w-full cursor-pointer transition-colors"
                        style={{
                          padding: "7px 10px",
                          borderRadius: 6,
                          border: "none",
                          background: "transparent",
                          color: "var(--text)",
                          fontSize: 12.5,
                          fontFamily: "var(--font-mono)",
                          textAlign: "left",
                          opacity: bindingAgent ? 0.5 : 1,
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                      >
                        <span
                          className="rounded-full shrink-0"
                          style={{ width: 5, height: 5, background: "var(--green)" }}
                        />
                        {a.agent_id}
                      </button>
                    ))}
                  </div>
                )}

                {/* Bound agents list */}
                {(channel.agents?.length ?? 0) === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-mono)", padding: "8px 0" }}>
                    No agents bound
                  </div>
                ) : (
                  <div className="flex flex-col" style={{ gap: 4 }}>
                    {channel.agents!.map((binding) => (
                      <div
                        key={binding.id}
                        className="flex items-center justify-between"
                        style={{
                          padding: "6px 10px",
                          borderRadius: 6,
                          background: "var(--bg-raised)",
                          border: "1px solid var(--border)",
                        }}
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className="rounded-full shrink-0"
                            style={{ width: 5, height: 5, background: "var(--green)" }}
                          />
                          <span style={{ fontSize: 12.5, fontFamily: "var(--font-mono)", color: "var(--text)" }}>
                            {binding.agent_name}
                          </span>
                          {binding.is_default && (
                            <span style={{ fontSize: 10, color: "var(--text-dim)", fontWeight: 500 }}>default</span>
                          )}
                          {binding.command && (
                            <span style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--purple)" }}>
                              {binding.command}
                            </span>
                          )}
                        </div>
                        <button
                          onClick={() => handleUnbind(binding.agent_name)}
                          className="cursor-pointer transition-colors"
                          style={{
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            border: "none",
                            background: "transparent",
                            color: "var(--text-dim)",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.background = "var(--red-dim)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-dim)"; e.currentTarget.style.background = "transparent"; }}
                          title="Unbind agent"
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                            <path d="M18 6L6 18M6 6l12 12" />
                          </svg>
                        </button>
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
                padding: "12px 20px",
                borderTop: "1px solid var(--border)",
              }}
            >
              {!confirmDelete ? (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="cursor-pointer transition-colors"
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: "var(--text-dim)",
                    fontSize: 12,
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "rgba(239,68,68,0.25)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-dim)"; e.currentTarget.style.borderColor = "var(--border)"; }}
                >
                  Delete channel
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  <span style={{ fontSize: 12, color: "var(--red)" }}>Delete?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="cursor-pointer"
                    style={{
                      padding: "4px 10px",
                      borderRadius: 5,
                      border: "none",
                      background: "var(--red)",
                      color: "#fff",
                      fontSize: 12,
                      fontWeight: 600,
                      opacity: deleting ? 0.5 : 1,
                    }}
                  >
                    {deleting ? "..." : "Yes"}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="cursor-pointer"
                    style={{ padding: "4px 10px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--text-muted)", fontSize: 12 }}
                  >
                    No
                  </button>
                </div>
              )}
              <button
                onClick={onClose}
                className="cursor-pointer transition-colors"
                style={{
                  padding: "6px 14px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "transparent",
                  color: "var(--text-secondary)",
                  fontSize: 12,
                }}
              >
                Done
              </button>
            </div>
          </>
        ) : null}
      </DialogShell>
    </>
  );
}

/* ── Shared Dialog Parts ────────────────────────────────────────────────── */

function Backdrop({ onClose }: { onClose: () => void }) {
  return (
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
  );
}

function DialogShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        position: "fixed",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        width: "100%",
        maxWidth: 460,
        background: "var(--bg-surface)",
        border: "1px solid var(--border-strong)",
        borderRadius: 12,
        zIndex: 201,
        overflow: "hidden",
        animation: "dialog-in 0.2s ease-out both",
      }}
    >
      {children}
    </div>
  );
}

function DialogHeader({ title, subtitle, onClose }: { title: string; subtitle: string; onClose: () => void }) {
  return (
    <div
      className="flex items-center justify-between"
      style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--text)", letterSpacing: "-0.02em" }}>
          {title}
        </h2>
        <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--text-muted)" }}>{subtitle}</p>
      </div>
      <button
        onClick={onClose}
        className="flex items-center justify-center cursor-pointer transition-colors"
        style={{ width: 28, height: 28, borderRadius: 6, border: "1px solid var(--border)", background: "transparent", color: "var(--text-muted)" }}
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-raised)"; e.currentTarget.style.color = "var(--text)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-muted)"; }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

/* ── Exports ────────────────────────────────────────────────────────────── */

export { CreateChannelDialog, ManageChannelDialog };
