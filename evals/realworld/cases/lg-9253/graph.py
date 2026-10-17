"""Minimal structure for langgraph#9253: retry around a node invoking two subgraphs.

    START -> node -> END          (compiled with InMemorySaver)
    node: research.invoke(...) ; raise ConnectionError ; review.invoke(...)

The node carries a RetryPolicy.  While the retries run out, the node dies
between the two subgraph calls; the recovered run should execute ``review``,
but in the reported behavior it resumes into ``research``'s checkpoint.

https://github.com/langchain-ai/langgraph/issues/9253
"""

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import RetryPolicy


class State(TypedDict):
    text: str


def make_subgraph(text: str):
    return (
        StateGraph(State)
        .add_node("n", lambda state: {"text": text})
        .add_edge(START, "n")
        .compile()
    )


research = make_subgraph("RESEARCH")
review = make_subgraph("REVIEW")

fail = {"active": True}


def node(state: State) -> dict:
    research.invoke({})
    if fail["active"]:
        raise ConnectionError("transient failure between the two subgraph calls")
    return review.invoke({})


graph = (
    StateGraph(State)
    .add_node("node", node, retry_policy=RetryPolicy())
    .add_edge(START, "node")
    .compile(checkpointer=InMemorySaver())
)


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "1"}}
    try:
        graph.invoke({}, config)
    except ConnectionError:
        fail["active"] = False
        # Expected "REVIEW"; reported behavior is "RESEARCH".
        print(graph.invoke(None, config)["text"])
