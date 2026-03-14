"""ninetrix trace — visualize a multi-agent run from agentfile_checkpoints."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.tree import Tree

console = Console()

# Model pricing: (input_cost_per_1M_tokens, output_cost_per_1M_tokens) in USD
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-6":             (15.00, 75.00),
    "claude-sonnet-4-6":            (3.00, 15.00),
    "claude-haiku-4-5-20251001":    (0.80,  4.00),
    "claude-haiku-4-5":             (0.80,  4.00),
    "claude-3-5-sonnet-20241022":   (3.00, 15.00),
    "claude-3-5-haiku-20241022":    (0.80,  4.00),
    # OpenAI
    "gpt-4o":                       (2.50, 10.00),
    "gpt-4o-mini":                  (0.15,  0.60),
    "o1":                          (15.00, 60.00),
    "o1-mini":                      (3.00, 12.00),
    # Google
    "gemini-2.5-flash":             (0.30,  1.25),
    "gemini-2.0-flash":             (0.10,  0.40),
    "gemini-1.5-pro":               (1.25,  5.00),
    # Groq / Mistral
    "llama-3.3-70b-versatile":      (0.59,  0.79),
    "mistral-large-latest":         (3.00,  9.00),
}


def _resolve_db_url(url_template: str) -> str:
    import re
    url = re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), ""), url_template)
    # host.docker.internal is only reachable from inside containers; on the host use localhost
    return url.replace("host.docker.internal", "localhost")


def _load_dotenv_key(key: str) -> str | None:
    env_file = Path(".env")
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _format_tokens(n: int | None) -> str:
    if not n:
        return "?"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _format_dur(ms: int | None) -> str:
    if not ms:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms/1000:.1f}s"


def _lookup_pricing(model: str) -> tuple[float, float] | None:
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    for key, val in _MODEL_PRICING.items():
        if key in model:
            return val
    return None


def _cost_usd(model: str, input_toks: int, output_toks: int, total_toks: int) -> float | None:
    """Return estimated cost in USD, or None if model is unknown."""
    pricing = _lookup_pricing(model)
    if pricing is None:
        return None
    in_price, out_price = pricing
    if input_toks or output_toks:
        return (input_toks / 1e6) * in_price + (output_toks / 1e6) * out_price
    # Fall back to blended rate when input/output split is unavailable
    return (total_toks / 1e6) * ((in_price + out_price) / 2)


def _fetch_records(conn, thread_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT trace_id, agent_id, step_index, status, timestamp,
               checkpoint, metadata, parent_trace_id
        FROM agentfile_checkpoints
        WHERE thread_id = %s
        ORDER BY timestamp ASC, step_index ASC
        """,
        (thread_id,),
    )
    rows = cur.fetchall()

    records = []
    agent_prev_len: dict[str, int] = {}
    agent_prev_ts: dict[str, object] = {}

    for row in rows:
        trace_id, agent_id, step_index, status, timestamp, checkpoint, metadata, parent_trace_id = row
        cp = checkpoint if isinstance(checkpoint, dict) else json.loads(checkpoint or "{}")
        md = metadata if isinstance(metadata, dict) else json.loads(metadata or "{}")

        tokens = md.get("tokens_used") or 0
        input_tokens = md.get("input_tokens") or 0
        output_tokens = md.get("output_tokens") or 0
        model = md.get("model", "")
        tool_durations: dict[str, int] = cp.get("variables", {}).get("tool_call_durations", {})
        ts_str = str(timestamp)[:19].replace("T", " ").replace("-", "/")[5:]

        # Step wall-clock duration from consecutive checkpoint timestamps per agent
        step_dur_ms: int | None = None
        prev_ts = agent_prev_ts.get(agent_id)
        if prev_ts is not None:
            try:
                t1 = timestamp.timestamp() if hasattr(timestamp, "timestamp") else float(str(timestamp)[:19])
                t0 = prev_ts.timestamp() if hasattr(prev_ts, "timestamp") else float(str(prev_ts)[:19])
                step_dur_ms = max(0, int((t1 - t0) * 1000))
            except Exception:
                pass
        agent_prev_ts[agent_id] = timestamp

        # Extract tool calls added in THIS step by diffing the history length
        history = cp.get("history", [])
        prev_len = agent_prev_len.get(agent_id, 0)
        new_msgs = history[prev_len:]
        agent_prev_len[agent_id] = len(history)

        tool_calls = []
        for msg in new_msgs:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "?")
                            inp = block.get("input", {})
                            snippet = json.dumps(inp)[:80] if inp else ""
                            tool_calls.append({
                                "name": name,
                                "snippet": snippet,
                                "dur_ms": tool_durations.get(name),
                            })

        records.append({
            "trace_id": trace_id,
            "agent_id": agent_id,
            "step_index": step_index,
            "status": status,
            "timestamp": ts_str,
            "tokens": tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
            "parent_trace_id": parent_trace_id,
            "tool_calls": tool_calls,
            "step_dur_ms": step_dur_ms,
        })

    return records


def _build_tree(thread_id: str, records: list[dict]) -> Tree:
    """Build a Rich Tree with step timing, tool calls, and cost estimates."""
    total_tokens = sum(r["tokens"] for r in records)
    total_input  = sum(r["input_tokens"] for r in records)
    total_output = sum(r["output_tokens"] for r in records)
    agents_seen  = {r["agent_id"] for r in records}
    primary_model = records[0]["model"] if records else ""

    cost = _cost_usd(primary_model, total_input, total_output, total_tokens) if primary_model else None
    cost_str = f"  ~${cost:.4f}" if cost is not None else ""

    header = (
        f"[bold purple]Thread {thread_id[:12]}…[/bold purple]  "
        f"[dim]{len(agents_seen)} agent(s)  {len(records)} step(s)  "
        f"{_format_tokens(total_tokens)} tok total{cost_str}[/dim]"
    )

    # Group steps by trace_id for tree nesting
    trace_steps: dict[str, list[dict]] = {}
    for rec in records:
        trace_steps.setdefault(rec["trace_id"], []).append(rec)

    all_parent_ids = {
        rec["parent_trace_id"]
        for recs in trace_steps.values()
        for rec in recs
        if rec["parent_trace_id"]
    }
    root_traces = [tid for tid in trace_steps if tid not in all_parent_ids] or list(trace_steps.keys())

    _STATUS_COLOR = {
        "completed": "green",
        "in_progress": "yellow",
        "error": "red",
        "waiting_for_approval": "yellow",
    }

    def _render_trace(tree_node, trace_id: str) -> None:
        for rec in trace_steps.get(trace_id, []):
            color = _STATUS_COLOR.get(rec["status"], "dim")
            dur_part  = f"  {_format_dur(rec['step_dur_ms'])}" if rec["step_dur_ms"] else ""
            step_cost = _cost_usd(rec["model"], rec["input_tokens"], rec["output_tokens"], rec["tokens"])
            cost_part = f"  ~${step_cost:.4f}" if step_cost is not None else ""
            label = (
                f"[bold]{rec['agent_id']}[/bold]  "
                f"step {rec['step_index']}  "
                f"[{color}]{rec['status']}[/{color}]  "
                f"[dim]{_format_tokens(rec['tokens'])} tok{dur_part}{cost_part}  {rec['timestamp']}[/dim]"
            )
            step_node = tree_node.add(label)

            # Tool call sub-nodes (Feature 4)
            for tc in rec["tool_calls"]:
                dur_str     = f" [{_format_dur(tc['dur_ms'])}]" if tc["dur_ms"] else ""
                snippet_str = f"  [dim]{tc['snippet']}[/dim]" if tc["snippet"] else ""
                step_node.add(f"[cyan]⚡ {tc['name']}[/cyan][dim]{dur_str}[/dim]{snippet_str}")

            # Recurse into child traces (sub-agents)
            child_traces = {
                tid for tid, recs in trace_steps.items()
                if any(r["parent_trace_id"] == trace_id for r in recs) and tid != trace_id
            }
            for child_tid in child_traces:
                _render_trace(step_node, child_tid)

    tree = Tree(header)
    for root_tid in root_traces:
        _render_trace(tree, root_tid)
    return tree


@click.command("trace")
@click.argument("thread_id")
@click.option("--db-url", default=None,
              help="PostgreSQL connection URL (overrides DATABASE_URL env var)")
@click.option("--follow", "-f", is_flag=True, default=False,
              help="Poll for new checkpoints and refresh the tree live (Ctrl+C to stop)")
@click.option("--interval", default=2.0, show_default=True,
              help="Polling interval in seconds (used with --follow)")
def trace_cmd(thread_id: str, db_url: str | None, follow: bool, interval: float) -> None:
    """Visualize a multi-agent run tree from the checkpoint database.

    THREAD_ID is the thread identifier used during the run.
    Use --follow / -f to auto-refresh while a run is in progress.
    """
    console.print()
    console.print("[bold purple]ninetrix trace[/bold purple]\n")

    if db_url is None:
        db_url = os.environ.get("DATABASE_URL") or _load_dotenv_key("DATABASE_URL")
    if db_url is None:
        console.print("[red]No database URL.[/red] Set DATABASE_URL or pass --db-url.\n")
        raise SystemExit(1)

    db_url = _resolve_db_url(db_url)

    try:
        import psycopg
    except ImportError:
        console.print(
            "[red]psycopg3 not installed.[/red] Run: pip install 'psycopg[binary]>=3.0'\n"
        )
        raise SystemExit(1)

    try:
        conn = psycopg.connect(db_url, autocommit=True)
    except Exception as exc:
        console.print(f"[red]Could not connect to database:[/red] {exc}\n")
        raise SystemExit(1)

    if not follow:
        records = _fetch_records(conn, thread_id)
        conn.close()
        if not records:
            console.print(f"  No checkpoints found for thread_id=[bold]{thread_id}[/bold]\n")
            return
        console.print(_build_tree(thread_id, records))
        console.print()
        return

    # ── Follow mode: live-refreshing tree (Feature 2) ────────────────────────
    console.print(f"  [dim]Following [bold]{thread_id}[/bold]  (Ctrl+C to stop)…[/dim]\n")
    try:
        with Live(console=console, refresh_per_second=1, vertical_overflow="visible") as live:
            prev_count = -1
            while True:
                try:
                    records = _fetch_records(conn, thread_id)
                except Exception:
                    records = []
                if records and len(records) != prev_count:
                    prev_count = len(records)
                    live.update(_build_tree(thread_id, records))
                elif not records:
                    live.update("[dim]  No checkpoints yet…[/dim]")
                time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n  Stopped.\n")
    finally:
        conn.close()
