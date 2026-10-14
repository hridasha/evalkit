"""A tiny app for the CLI tests: it knows a few facts and says so when it does not."""

FACTS = {"capital of france": "Paris", "capital of peru": "Lima", "capital of japan": "Tokyo"}


def answer(question: str) -> dict:
    for key, value in FACTS.items():
        if key in question.lower():
            return {"answer": value, "contexts": [{"id": "geo", "text": f"{key} is {value}"}]}
    return {"answer": "I don't know.", "contexts": []}
