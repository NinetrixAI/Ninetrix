"""
Timing tools for the benchmark agent.

Each tool simulates a realistic workload with a configurable or fixed delay
so you can observe tool-call latency in the Ninetrix observability dashboard.
The measured wall-clock time is included in every response so the LLM can
report it back to the user.
"""

import time
import random

from ninetrix import Tool


@Tool(
    name="fast_lookup",
    description=(
        "Simulate a fast in-memory cache lookup. "
        "Sleeps ~50 ms and returns the value for the given key."
    ),
)
def fast_lookup(key: str) -> dict:
    """Simulated fast cache lookup (~50 ms).

    Args:
        key: Cache key to look up.
    """
    start = time.perf_counter()

    # Simulate a very fast in-memory hit
    jitter_ms = random.uniform(40, 60)
    time.sleep(jitter_ms / 1000)

    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "key": key,
        "value": f"cached_value_for_{key}",
        "hit": True,
        "elapsed_ms": round(elapsed_ms, 2),
    }


@Tool(
    name="slow_fetch",
    description=(
        "Simulate a slow external API call. "
        "Sleeps between 800 ms and 1200 ms and returns a payload."
    ),
)
def slow_fetch(url: str) -> dict:
    """Simulated slow remote fetch (800–1200 ms).

    Args:
        url: The URL to fetch (simulated — no real HTTP request is made).
    """
    start = time.perf_counter()

    delay_ms = random.uniform(800, 1200)
    time.sleep(delay_ms / 1000)

    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "url": url,
        "status_code": 200,
        "body": f"<simulated response from {url}>",
        "elapsed_ms": round(elapsed_ms, 2),
    }


@Tool(
    name="compute_fibonacci",
    description=(
        "Compute the nth Fibonacci number using a deliberate recursive algorithm "
        "to burn CPU time and make the compute latency observable."
    ),
)
def compute_fibonacci(n: int) -> dict:
    """CPU-bound Fibonacci computation — deliberately slow for n >= 30.

    Args:
        n: Which Fibonacci number to compute (recommend 28–35 for measurable delay).
    """
    if n < 0:
        raise ValueError("n must be >= 0")

    start = time.perf_counter()

    def _fib(x: int) -> int:
        if x <= 1:
            return x
        return _fib(x - 1) + _fib(x - 2)

    result = _fib(min(n, 38))  # cap at 38 to avoid runaway times
    elapsed_ms = (time.perf_counter() - start) * 1000

    return {
        "n": n,
        "result": result,
        "elapsed_ms": round(elapsed_ms, 2),
        "note": "recursive (intentionally slow) — good for CPU latency testing",
    }


@Tool(
    name="batch_process",
    description=(
        "Simulate processing a batch of items. "
        "Each item takes ~200 ms, so a batch of 5 items takes ~1 s total."
    ),
)
def batch_process(items: list, delay_per_item_ms: int = 200) -> dict:
    """Simulate sequential batch processing with per-item delay.

    Args:
        items: List of item names to process.
        delay_per_item_ms: How many milliseconds to spend on each item (default 200).
    """
    if not items:
        return {"processed": [], "total_items": 0, "elapsed_ms": 0.0}

    delay_per_item_ms = max(10, min(delay_per_item_ms, 2000))  # clamp 10–2000 ms

    start = time.perf_counter()
    results = []

    for item in items:
        item_start = time.perf_counter()
        jitter = random.uniform(0.85, 1.15)
        time.sleep((delay_per_item_ms * jitter) / 1000)
        item_elapsed = (time.perf_counter() - item_start) * 1000
        results.append({"item": item, "status": "ok", "elapsed_ms": round(item_elapsed, 2)})

    total_elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "processed": results,
        "total_items": len(items),
        "elapsed_ms": round(total_elapsed_ms, 2),
        "avg_per_item_ms": round(total_elapsed_ms / len(items), 2),
    }
