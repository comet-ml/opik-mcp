# mypy: ignore-errors
"""A tiny support agent, instrumented with Opik. It has one real bug.

Run it to produce a trace that /opik-explain should be able to root-cause.
"""

import opik
from opik import opik_context

DOCS = {
    "refund": "Refunds are processed within 5-7 business days.",
    "shipping": "Standard shipping takes 3-5 business days.",
}


@opik.track(type="tool")
def retrieve(query: str) -> str:
    # BUG: looks the doc up by the *whole* query string instead of matching a
    # keyword, so a real question never hits a key and retrieval comes back empty.
    return DOCS.get(query, "No relevant docs found.")


@opik.track(type="llm")
def generate(question: str, context: str) -> str:
    # Answers confidently even when context is empty -> ungrounded / wrong.
    return "Your refund will be processed within 24 hours."


@opik.track
def answer(question: str) -> str:
    trace = opik_context.get_current_trace_data()
    if trace is not None:
        _captured["trace_id"] = trace.id
    context = retrieve(question)
    return generate(question, context)


_captured: dict[str, str] = {}

if __name__ == "__main__":
    print("ANSWER:", answer("what is your refund window?"))
    opik.flush_tracker()
    print("TRACE_ID:", _captured.get("trace_id", "<none>"))
