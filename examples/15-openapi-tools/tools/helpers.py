"""Local helper tools for the API assistant."""

from ninetrix import Tool


@Tool
def format_json(data: str, indent: int = 2) -> str:
    """Pretty-format a JSON string for display.

    Args:
        data: Raw JSON string to format.
        indent: Number of spaces for indentation.
    """
    import json
    parsed = json.loads(data)
    return json.dumps(parsed, indent=indent, sort_keys=True)


@Tool
def summarize_response(status_code: int, body: str, max_length: int = 200) -> str:
    """Summarize an HTTP response for the user.

    Args:
        status_code: HTTP status code.
        body: Response body text.
        max_length: Maximum length of the summary.
    """
    status = "OK" if status_code < 400 else "ERROR"
    truncated = body[:max_length] + "..." if len(body) > max_length else body
    return f"[{status_code} {status}] {truncated}"
