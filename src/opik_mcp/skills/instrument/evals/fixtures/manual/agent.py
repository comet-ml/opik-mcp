"""A tiny customer-support agent. No observability, no LLM framework."""


def retrieve(query: str) -> str:
    docs = {
        "refund": "Refunds are processed within 5-7 business days.",
        "shipping": "Standard shipping takes 3-5 business days.",
    }
    for keyword, snippet in docs.items():
        if keyword in query.lower():
            return snippet
    return "No relevant docs found."


def generate(question: str, context: str) -> str:
    # Local stand-in for an LLM (no provider configured).
    return f"Answer to '{question}': {context}"


def answer(question: str) -> str:
    context = retrieve(question)
    return generate(question, context)


if __name__ == "__main__":
    print(answer("What is your refund window?"))
