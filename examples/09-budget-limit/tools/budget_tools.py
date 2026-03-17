"""
Tools for the budget-limit example.

Returns progressively longer text blobs so the LLM spends more tokens
with each successive summarisation step.
"""

import textwrap

from ninetrix import Tool

_TEXTS = {
    "short": textwrap.dedent("""\
        Artificial intelligence is changing how we build software. Models
        now write code, review pull requests, and debug production incidents.
        Teams that adopt AI tooling early tend to ship faster.
    """),

    "medium": textwrap.dedent("""\
        The history of computing is punctuated by platform shifts. Each shift
        — from mainframes to minicomputers, from minicomputers to personal
        computers, from PCs to the web, from the web to mobile — rewarded
        builders who recognised the transition early and penalised incumbents
        who clung to the prior paradigm.

        Large language models represent the latest such shift. For the first
        time, software can understand and generate natural language at a level
        that is useful in production contexts: customer support, code review,
        document analysis, and increasingly autonomous task execution.

        The companies that will win the next decade are the ones building the
        infrastructure layer for this new paradigm — not the models themselves,
        but the tooling, orchestration, and trust layer that makes models
        reliable enough to run unsupervised in production.
    """),

    "long": textwrap.dedent("""\
        Agent frameworks are proliferating faster than developers can evaluate
        them. LangChain, AutoGen, CrewAI, LlamaIndex, Haystack, Semantic
        Kernel — each promises to simplify multi-step LLM workflows, yet each
        introduces its own abstractions, DSLs, and failure modes.

        The core problem they are all trying to solve is the same: how do you
        take a stateless, token-in/token-out language model and turn it into
        something that can reliably complete a multi-step task over minutes or
        hours, survive interruptions, call external tools, and hand off work to
        other agents?

        The answers diverge at the orchestration layer. Some frameworks use
        code graphs (nodes + edges). Others use prompt chaining. A third class
        treats agents as long-running processes that maintain their own state
        and communicate via message queues.

        Each model has tradeoffs. Code graphs are easy to visualise and audit
        but brittle when the LLM output deviates from the expected schema.
        Prompt chains are flexible but hard to observe. Long-running processes
        require infrastructure (queues, checkpoints, restart logic) but scale
        better and are easier to debug with standard tools.

        The market has not yet converged on a standard. The winning abstraction
        will be the one that maps most naturally onto concepts developers
        already understand — which is why Docker-style declarative YAML
        definitions are a credible bet.
    """),

    "very_long": textwrap.dedent("""\
        Observability is the single most under-invested area in AI engineering
        today. Teams instrument every database query and HTTP request with
        distributed traces, structured logs, and metrics dashboards — then
        deploy LLM-powered agents with essentially zero visibility into what
        the model is thinking, which tools it called, how many tokens it burned,
        and why it produced the output it did.

        This asymmetry is a product maturity problem, not a technical one. The
        OpenTelemetry ecosystem provides everything needed to trace agent
        executions: spans, attributes, events, baggage propagation across
        service boundaries. What is missing is convention: an agreed-upon span
        schema for LLM tool calls, a standard attribute namespace for token
        counts and model names, and a set of default dashboards that work out
        of the box.

        Without observability, AI teams operate in the dark. A model that
        silently consumes $50 of API credits on a single run because it entered
        a retry loop is indistinguishable from a model that completed its task
        efficiently — until the billing dashboard updates the next morning.

        The fix is instrumentation at the framework level, not the application
        level. Every tool call should be a traced span. Every checkpoint should
        carry a parent trace ID. Budget consumption should be a first-class
        metric exposed via /metrics in Prometheus format, not a log line buried
        in stdout.

        The business case for observability investment is straightforward: teams
        that can see what their agents are doing iterate faster, debug incidents
        faster, and build confidence with stakeholders faster. Observability is
        the difference between "our AI does something useful" and "our AI does
        exactly what we expect, and we can prove it."

        Self-hosted deployments make this even more critical. When agent traffic
        never leaves the corporate network, the observability stack must also run
        on-premise. That means OpenTelemetry collectors, Grafana, Prometheus, and
        a checkpoint database — all deployed alongside the agent runtime. Teams
        that get this right unlock the regulated-industry market: healthcare,
        finance, government, and defence, all of whom are desperate for AI
        tooling they can actually audit.
    """),
}


@Tool(
    name="generate_text",
    description=(
        "Return a text passage of the requested length for summarisation practice. "
        "Valid lengths: 'short', 'medium', 'long', 'very_long'."
    ),
)
def generate_text(length: str) -> dict:
    """Return a text blob of the requested length.

    Args:
        length: One of 'short', 'medium', 'long', or 'very_long'.
    """
    length = length.strip().lower()
    if length not in _TEXTS:
        return {
            "error": f"Unknown length '{length}'. Choose from: short, medium, long, very_long.",
        }

    text = _TEXTS[length]
    word_count = len(text.split())
    return {
        "length": length,
        "word_count": word_count,
        "text": text,
    }
