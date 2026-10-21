"""A tiny, deliberately UNINSTRUMENTED agent.

Golden path: run() -> retrieve() (a tool) -> generate() (an LLM call).
Correctly instrumented, one representative run should produce a 3-span trace:
    general (run)  ->  tool (retrieve)  +  llm (generate)

The skill under test must add Opik tracing, run this safely once, and verify
that a real, complete trace landed. Running needs an OpenAI-compatible key.
"""

from __future__ import annotations

import os

from openai import OpenAI

client = OpenAI()

_CORPUS = {"opik": "Opik is an LLM observability tool for tracing and evaluating LLM apps."}


def retrieve(query: str) -> str:
    """Tool: look up context for the query (deterministic, no network)."""
    return _CORPUS.get(query.lower().split()[0], "no context found")


def generate(question: str, context: str) -> str:
    """LLM call: answer the question using the retrieved context."""
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


def run(question: str) -> str:
    """Entrypoint: retrieve context, then generate an answer."""
    context = retrieve(question)
    return generate(question, context)


if __name__ == "__main__":
    print(run("What is opik?"))
