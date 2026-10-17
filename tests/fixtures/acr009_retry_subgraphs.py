"""ACR009 positive: retry_policy on a node invoking two subgraphs."""

from typing import TypedDict

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


def node(state: State) -> dict:
    research.invoke({})
    return review.invoke({})


graph = (
    StateGraph(State)
    .add_node("node", node, retry_policy=RetryPolicy())
    .add_edge(START, "node")
    .compile()
)
