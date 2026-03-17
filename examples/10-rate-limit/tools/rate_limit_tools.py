"""
Tools for the rate-limit example.

Provides a simple question dispenser so the agent has a concrete reason
to make multiple LLM calls in rapid succession, making the rate_limit
governor's throttle delays visible in the logs.
"""

import time

from ninetrix import Tool

_QUESTIONS = [
    "What is the capital of France?",
    "What is 17 multiplied by 13?",
    "Name three famous Renaissance painters.",
    "What is the boiling point of water in Celsius?",
    'Who wrote "Pride and Prejudice"?',
    "What programming language was the Linux kernel written in?",
]

_call_log: list[dict] = []


@Tool(
    name="get_question",
    description=(
        "Return the question at the given 1-based index (1–6). "
        "Also records a timestamp so the agent can report call timing."
    ),
)
def get_question(index: int) -> dict:
    """Fetch question number `index` (1-based).

    Args:
        index: Which question to retrieve (1 to 6).
    """
    if not (1 <= index <= len(_QUESTIONS)):
        return {
            "error": f"Index {index} out of range. Valid range: 1–{len(_QUESTIONS)}.",
        }

    ts = time.time()
    question = _QUESTIONS[index - 1]

    _call_log.append({"index": index, "ts": ts})

    # Show inter-call gap if we have a previous entry
    gap_info = None
    if len(_call_log) >= 2:
        gap_sec = ts - _call_log[-2]["ts"]
        gap_info = f"{gap_sec:.2f}s since last fetch"

    return {
        "index": index,
        "total_questions": len(_QUESTIONS),
        "question": question,
        "fetched_at": time.strftime("%H:%M:%S", time.localtime(ts)),
        "gap_since_last_fetch": gap_info,
        "hint": (
            "Answer this question, then call get_question with the next index."
            if index < len(_QUESTIONS)
            else "This is the last question."
        ),
    }


@Tool(
    name="get_call_summary",
    description=(
        "Return a summary of all get_question calls made so far, including "
        "timestamps and inter-call gaps. Useful for observing rate-limit throttle delays."
    ),
)
def get_call_summary() -> dict:
    """Return a timing summary of every get_question call in this run."""
    if not _call_log:
        return {"message": "No questions fetched yet.", "calls": []}

    rows = []
    for i, entry in enumerate(_call_log):
        gap = (
            round(entry["ts"] - _call_log[i - 1]["ts"], 2)
            if i > 0
            else None
        )
        rows.append({
            "call_number": i + 1,
            "question_index": entry["index"],
            "time": time.strftime("%H:%M:%S", time.localtime(entry["ts"])),
            "gap_from_previous_sec": gap,
        })

    total_elapsed = _call_log[-1]["ts"] - _call_log[0]["ts"] if len(_call_log) > 1 else 0
    return {
        "total_fetches": len(_call_log),
        "total_elapsed_sec": round(total_elapsed, 2),
        "calls": rows,
        "note": (
            "Gaps longer than ~0.3 s are caused by the rate_limit governor "
            "throttling LLM requests — not by the tool itself."
        ),
    }
