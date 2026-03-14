import type { TimelineEvent, ThreadSummary } from "./api";

export type TraceNodeType = "llm" | "tool" | "thinking" | "handoff";
export type TraceNodeStatus = "success" | "error" | "running";

export interface TraceNode {
  id: string;
  parentId: string | null;
  depth: number;
  type: TraceNodeType;
  label: string;
  agentId: string;
  status: TraceNodeStatus;
  startOffsetMs: number;
  durationMs: number | null;
  absoluteStartIso: string;
  absoluteEndIso: string | null;
  model: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  estimatedCostUsd: number | null;
  llmContent: { prompt: string; response: string } | null;
  toolContent: { input: string; output: string } | null;
  thinkingContent: { text: string } | null;
  handoffContent: { targetAgent: string; message: string } | null;
}

function estimateCost(model: string, inputTokens: number, outputTokens: number): number {
  const pricing: Array<[string, number, number]> = [
    ["claude-opus", 15, 75],
    ["claude-sonnet", 3, 15],
    ["claude-haiku", 0.25, 1.25],
    ["gpt-4o", 5, 15],
    ["gpt-4", 30, 60],
    ["gpt-3.5", 0.5, 1.5],
  ];
  for (const [key, inp, out] of pricing) {
    if (model.toLowerCase().includes(key)) {
      return (inputTokens * inp + outputTokens * out) / 1_000_000;
    }
  }
  return (inputTokens * 3 + outputTokens * 15) / 1_000_000;
}

export function timelineEventsToTraceNodes(
  events: TimelineEvent[],
  thread: ThreadSummary
): TraceNode[] {
  if (!events.length) return [];

  const baseTs = new Date(events[0].ts).getTime();

  // Build trace_id → agent_id mapping
  const traceToAgent = new Map<string, string>();
  const agentParentTrace = new Map<string, string | null>();
  const agentTraceId = new Map<string, string>();

  for (const ev of events) {
    traceToAgent.set(ev.trace_id, ev.agent_id);
    if (!agentParentTrace.has(ev.agent_id)) {
      agentParentTrace.set(ev.agent_id, ev.parent_trace_id);
    }
    agentTraceId.set(ev.agent_id, ev.trace_id);
  }

  // Compute depth for each agent recursively
  const agentDepths = new Map<string, number>();
  function getDepth(agentId: string, visited = new Set<string>()): number {
    if (agentDepths.has(agentId)) return agentDepths.get(agentId)!;
    if (visited.has(agentId)) return 0; // cycle guard
    visited.add(agentId);

    const parentTraceId = agentParentTrace.get(agentId);
    if (!parentTraceId) {
      agentDepths.set(agentId, 0);
      return 0;
    }
    const parentAgentId = traceToAgent.get(parentTraceId);
    if (!parentAgentId) {
      agentDepths.set(agentId, 1);
      return 1;
    }
    const depth = getDepth(parentAgentId, visited) + 1;
    agentDepths.set(agentId, depth);
    return depth;
  }
  for (const agentId of agentParentTrace.keys()) {
    getDepth(agentId);
  }

  const nodes: TraceNode[] = [];
  let counter = 0;
  // Per-agent FIFO queue of tool nodes awaiting their result.
  // Keyed by agent_id; falls back to FIFO when tool_name is null on tool_result.
  const pendingToolQueues = new Map<string, TraceNode[]>();
  const pendingUserContent = new Map<string, string>(); // per-agent

  for (const ev of events) {
    const offsetMs = new Date(ev.ts).getTime() - baseTs;
    const depth = agentDepths.get(ev.agent_id) ?? 0;
    const makeId = () => `n${counter++}`;

    if (ev.type === "user_message") {
      pendingUserContent.set(ev.agent_id, ev.content);
      continue;
    }

    if (ev.type === "thinking") {
      nodes.push({
        id: makeId(),
        parentId: null,
        depth,
        type: "thinking",
        label: "Reasoning",
        agentId: ev.agent_id,
        status: "success",
        startOffsetMs: offsetMs,
        durationMs: ev.duration_ms ?? null,
        absoluteStartIso: ev.ts,
        absoluteEndIso: null,
        model: null,
        inputTokens: ev.tokens_in ?? null,
        outputTokens: ev.tokens_out ?? null,
        estimatedCostUsd: null,
        llmContent: null,
        toolContent: null,
        thinkingContent: { text: ev.content },
        handoffContent: null,
      });
      continue;
    }

    if (ev.type === "assistant_message") {
      const inputTok = ev.tokens_in ?? null;
      const outputTok = ev.tokens_out ?? null;
      const cost =
        inputTok != null && outputTok != null
          ? estimateCost(thread.model, inputTok, outputTok)
          : null;
      nodes.push({
        id: makeId(),
        parentId: null,
        depth,
        type: "llm",
        label: ev.agent_id,
        agentId: ev.agent_id,
        status: "success",
        startOffsetMs: offsetMs,
        durationMs: ev.duration_ms ?? null,
        absoluteStartIso: ev.ts,
        absoluteEndIso: null,
        model: thread.model || null,
        inputTokens: inputTok,
        outputTokens: outputTok,
        estimatedCostUsd: cost,
        llmContent: {
          prompt: pendingUserContent.get(ev.agent_id) ?? "",
          response: ev.content,
        },
        toolContent: null,
        thinkingContent: null,
        handoffContent: null,
      });
      pendingUserContent.delete(ev.agent_id);
      continue;
    }

    if (ev.type === "tool_call") {
      const isHandoff = ev.tool_name === "transfer_to_agent" || !!ev.target_agent;

      if (isHandoff) {
        nodes.push({
          id: makeId(),
          parentId: null,
          depth,
          type: "handoff",
          label: `→ ${ev.target_agent ?? "agent"}`,
          agentId: ev.agent_id,
          status: "success",
          startOffsetMs: offsetMs,
          durationMs: ev.duration_ms ?? null,
          absoluteStartIso: ev.ts,
          absoluteEndIso: null,
          model: null,
          inputTokens: null,
          outputTokens: null,
          estimatedCostUsd: null,
          llmContent: null,
          toolContent: null,
          thinkingContent: null,
          handoffContent: {
            targetAgent: ev.target_agent ?? "unknown",
            message: ev.content,
          },
        });
      } else {
        const node: TraceNode = {
          id: makeId(),
          parentId: null,
          depth,
          type: "tool",
          label: ev.tool_name ?? "tool",
          agentId: ev.agent_id,
          status: "running",
          startOffsetMs: offsetMs,
          durationMs: null,
          absoluteStartIso: ev.ts,
          absoluteEndIso: null,
          model: null,
          inputTokens: null,
          outputTokens: null,
          estimatedCostUsd: null,
          llmContent: null,
          toolContent: { input: ev.content, output: "" },
          thinkingContent: null,
          handoffContent: null,
        };
        const queue = pendingToolQueues.get(ev.agent_id) ?? [];
        queue.push(node);
        pendingToolQueues.set(ev.agent_id, queue);
        nodes.push(node);
      }
      continue;
    }

    if (ev.type === "tool_result") {
      const queue = pendingToolQueues.get(ev.agent_id) ?? [];
      // Match by tool_name if available, otherwise pop the oldest pending tool (FIFO)
      let pendingIdx = ev.tool_name
        ? queue.findIndex((n) => n.label === ev.tool_name)
        : 0;
      if (pendingIdx < 0) pendingIdx = 0; // fallback to FIFO if name not found
      const pending = queue[pendingIdx];
      if (pending?.toolContent) {
        pending.toolContent.output = ev.content;
        const isError = ev.content.startsWith("Tool error:") || ev.content.startsWith("Error:");
        pending.status = isError ? "error" : "success";
        pending.durationMs = ev.duration_ms ?? null;
        queue.splice(pendingIdx, 1);
      }
      continue;
    }
  }

  // Mark last node running if thread still in progress
  if (
    thread.status === "in_progress" ||
    thread.status === "running" ||
    thread.status === "started"
  ) {
    const last = nodes[nodes.length - 1];
    if (last) last.status = "running";
  }

  return nodes;
}

export function calcTotalMs(nodes: TraceNode[]): number {
  let max = 0;
  for (const n of nodes) {
    const end = n.startOffsetMs + (n.durationMs ?? 0);
    if (end > max) max = end;
  }
  return max || 1;
}
