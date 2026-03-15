"""
Custom tools for the data assistant agent.

These functions are automatically discovered by `ninetrix build` and
bundled into the Docker image. The LLM can call them just like MCP tools.
"""

from ninetrix import Tool


@Tool
def calculate_stats(numbers: list, metric: str = "mean") -> float:
    """Compute a basic statistic over a list of numbers.

    Args:
        numbers: List of numeric values to analyse.
        metric: One of 'mean', 'median', 'min', 'max', 'sum'.
    """
    if not numbers:
        return 0.0
    n = [float(x) for x in numbers]
    if metric == "mean":
        return sum(n) / len(n)
    if metric == "median":
        s = sorted(n)
        mid = len(s) // 2
        return (s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2)
    if metric == "min":
        return min(n)
    if metric == "max":
        return max(n)
    if metric == "sum":
        return sum(n)
    raise ValueError(f"Unknown metric: {metric!r}")


@Tool
def format_table(rows: list, headers: list) -> str:
    """Format a list of rows as a plain-text table.

    Args:
        rows: List of row lists (each row must match the header count).
        headers: Column header names.
    """
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_row = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    lines = [sep, header_row, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)) + " |")
    lines.append(sep)
    return "\n".join(lines)


@Tool(name="lookup_exchange_rate", description="Return a simulated exchange rate between two currencies.")
def lookup_rate(from_currency: str, to_currency: str) -> dict:
    """Simulated exchange-rate lookup (replace with a real API call).

    Args:
        from_currency: Source currency code, e.g. 'USD'.
        to_currency: Target currency code, e.g. 'EUR'.
    """
    # Toy static table — swap for a real forex API in production
    _rates: dict = {
        ("USD", "EUR"): 0.92,
        ("USD", "GBP"): 0.79,
        ("EUR", "USD"): 1.09,
        ("GBP", "USD"): 1.27,
    }
    rate = _rates.get((from_currency.upper(), to_currency.upper()))
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "rate": rate,
        "available": rate is not None,
    }


@Tool(name="magic_number", description="Return a magic number.")
def magic_number() -> int:
    """Return a magic number."""
    print("magic_number called")
    return 42