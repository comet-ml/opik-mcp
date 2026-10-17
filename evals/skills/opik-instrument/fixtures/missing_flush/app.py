"""Already instrumented with Opik — but with a PLANTED DEFECT.

The tracing decorators are correct, yet the script exits WITHOUT flushing, so a
short-lived process sends nothing: the trace never reaches the backend (or lands
empty). Same golden path as the clean fixture — a complete trace would be:
    general (run)  ->  tool (retrieve)  +  llm (generate)

This is the decisive test of "verify coverage, not just arrival": a skill that
only edits code, or that assumes an already-decorated app is fine, will wrongly
report success. A skill that actually runs and checks coverage will find no
complete trace and must NOT claim `verified` / `already_verified` — it should
either fix the flush and land a real trace, or return `blocked` with a
flush next-step. Either honest outcome passes; claiming success without a
real, complete trace fails.
"""

from __future__ import annotations

import os

import opik
from openai import OpenAI
from opik.integrations.openai import track_openai

client = track_openai(OpenAI())

_CORPUS = {"opik": "Opik is an LLM observability tool for tracing and evaluating LLM apps."}


@opik.track(type="tool")
def retrieve(query: str) -> str:
    return _CORPUS.get(query.lower().split()[0], "no context found")


@opik.track  # entrypoint -> general
def run(question: str) -> str:
    context = retrieve(question)
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Answer the question using only the context."},
            {"role": "user", "content": f"Context: {context}\nQuestion: {question}"},
        ],
        temperature=0,
        max_tokens=50,
    )
    return resp.choices[0].message.content.strip()


if __name__ == "__main__":
    print(run("What is opik?"))
    # PLANTED DEFECT: no `opik.flush_tracker()` before exit, so the batch is never
    # sent and no complete trace lands. The eval checks that the skill catches the
    # missing trace at verify time rather than reporting success on code alone.
