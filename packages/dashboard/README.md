# tracepad

Local observability dashboard for [Ninetrix](https://ninetrix.io) agent runs.

Tracepad connects to your local `api/` server and gives you a real-time window into every agent thread — LLM calls, tool invocations, handoffs between agents, token usage, timing, and full input/output at every step.

---

## What it does

- **Thread list** — every run with status, agent, model, token count, duration, and trigger type
- **Trace view** — nested tree of every step: LLM prompts/responses, tool inputs/outputs, agent handoffs
- **Timeline view** — Gantt chart of the run, color-coded by node type with millisecond precision
- **Raw events** — full JSON of the underlying timeline events from the API
- **Live mode** — auto-polls every 5 seconds; open threads poll every 3 seconds
- **Light/dark mode** — persists across sessions
- **API health indicator** — shows connection status and latency to the local API

---

## Prerequisites

The local API must be running before tracepad can show any data.

```bash
cd api
pip install -e .
cp .env.example .env   # set DATABASE_URL=postgresql://...
uvicorn main:app --reload --port 8000
```

---

## Running tracepad

```bash
cd local-app
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## Configuration

By default tracepad connects to `http://localhost:8000`. Override with an environment variable:

```bash
NEXT_PUBLIC_API_URL=http://localhost:9000 npm run dev
```

---

## Architecture

```
local-app/
├── src/
│   ├── app/
│   │   ├── layout.tsx          # Root layout — fonts, theme init script
│   │   ├── globals.css         # Design tokens, light/dark CSS variables
│   │   ├── page.tsx            # Redirects → /threads
│   │   └── threads/
│   │       ├── page.tsx        # Server wrapper
│   │       └── ThreadsClient.tsx  # Full dashboard — table, trace drawer, nav
│   ├── components/
│   │   └── ThemeToggle.tsx     # Light/dark pill toggle
│   └── lib/
│       ├── api.ts              # API client (listThreads, getThreadTimeline)
│       └── trace.ts            # TimelineEvent[] → TraceNode[] converter
```

**Data flow:**

```
agentfile run
    │
    └── agent container → POST /v1/runners/events → api/ → PostgreSQL
                                                              │
                                                         tracepad polls
                                                    GET /threads
                                                    GET /threads/{id}/timeline
```

---

## Node types

| Symbol | Type | Color | Content |
|--------|------|-------|---------|
| ◈ | LLM | Blue | Model name, prompt, response, token counts, estimated cost |
| ◎ | Tool | Green | Tool name, input arguments, output result, duration |
| ◀▶ | Handoff | Violet | Target agent name, transfer message |
| ◉ | Thinking | Amber | Reasoning text |

---

## Tech

- **Next.js 16** (App Router)
- **Tailwind CSS v4**
- **Fonts:** Syne · DM Sans · JetBrains Mono
- No component library — all UI is hand-built
