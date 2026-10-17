"""Minimal structure for langgraph#1950: class node with private schemas.

    START -> generate_query -> retrieve_documents -> generate -> END

RetrieveDocumentsNode takes QueryOutputState and returns DocumentOutputState,
but it is registered without a node input schema, so the graph cannot map the
update back onto OverallState.

https://github.com/langchain-ai/langgraph/issues/1950
"""

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class OverallState(TypedDict):
    question: str
    answer: str


class QueryOutputState(TypedDict):
    query: str


class DocumentOutputState(TypedDict):
    docs: list[str]


def generate_query(state: OverallState) -> dict:
    return {"query": state["question"][:2]}


class RetrieveDocumentsNode:
    def __call__(self, state: QueryOutputState) -> DocumentOutputState:
        return {"docs": [state["query"]] * 2}


def generate(state: dict) -> dict:
    return {"answer": "\n\n".join(state["docs"] + [state["question"]])}


graph = StateGraph(OverallState)
graph.add_node("generate_query", generate_query)
# Fix from the issue thread: pass input_schema=QueryOutputState here.
graph.add_node("retrieve_documents", RetrieveDocumentsNode())
graph.add_node("generate", generate)
graph.add_edge(START, "generate_query")
graph.add_edge("generate_query", "retrieve_documents")
graph.add_edge("retrieve_documents", "generate")
graph.add_edge("generate", END)
compiled = graph.compile()


if __name__ == "__main__":
    print(compiled.invoke({"question": "foo", "answer": ""}))
