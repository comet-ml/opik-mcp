"""Already partly instrumented: retrieve/generate are tracked, but the
entrypoint is not marked and there is no flush. Expect an AUDIT that adds the
missing pieces WITHOUT re-instrumenting retrieve/generate."""

import opik


@opik.track(type="tool")
def retrieve(query: str) -> str:
    docs = {"refund": "Refunds are processed within 5-7 business days."}
    for keyword, snippet in docs.items():
        if keyword in query.lower():
            return snippet
    return "No relevant docs found."


@opik.track(type="llm")
def generate(question: str, context: str) -> str:
    return f"Answer to '{question}': {context}"


def answer(question: str) -> str:  # not marked as entrypoint
    context = retrieve(question)
    return generate(question, context)


if __name__ == "__main__":
    print(answer("What is your refund window?"))  # no flush
