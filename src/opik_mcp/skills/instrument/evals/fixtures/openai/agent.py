"""A tiny customer-support agent backed by OpenAI. No observability."""

from openai import OpenAI

client = OpenAI()


def retrieve(query: str) -> str:
    docs = {"refund": "Refunds are processed within 5-7 business days."}
    for keyword, snippet in docs.items():
        if keyword in query.lower():
            return snippet
    return "No relevant docs found."


def generate(question: str, context: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Answer using only the provided context."},
            {"role": "user", "content": f"Context: {context}\n\nQuestion: {question}"},
        ],
    )
    return resp.choices[0].message.content


def answer(question: str) -> str:
    context = retrieve(question)
    return generate(question, context)


if __name__ == "__main__":
    print(answer("What is your refund window?"))
