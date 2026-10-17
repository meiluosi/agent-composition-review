"""ACR009 negative: two subgraph invokes without retry_policy."""

from typing import TypedDict

from langgraph.graph import START, StateGraph


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


graph = StateGraph(State)
graph.add_node("node", node)
graph.add_edge(START, "node")
graph.compile()
