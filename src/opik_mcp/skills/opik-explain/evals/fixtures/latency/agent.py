# mypy: ignore-errors
"""A tiny agent, instrumented with Opik. One span is slow.

Run it to produce a trace that /opik-explain should root-cause as a latency problem.
"""

import time

import opik
from opik import opik_context


@opik.track(type="tool")
def fetch_context(query: str) -> str:
    # Simulates a slow external call (e.g. an un-cached vector search).
    time.sleep(3.0)
    return "Context about: " + query


@opik.track(type="llm")
def generate(question: str, context: str) -> str:
    return f"Answer using {context}"


@opik.track
def answer(question: str) -> str:
    trace = opik_context.get_current_trace_data()
    if trace is not None:
        _captured["trace_id"] = trace.id
    context = fetch_context(question)
    return generate(question, context)


_captured: dict[str, str] = {}

if __name__ == "__main__":
    print("ANSWER:", answer("summarize the refund policy"))
    opik.flush_tracker()
    print("TRACE_ID:", _captured.get("trace_id", "<none>"))
