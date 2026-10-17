"""ACR007 positive: a function node returns a key the state does not declare."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int


def node(state: State) -> dict:
    # ``undeclared_key`` is silently filtered at the node boundary.
    return {"x": 1, "undeclared_key": "this gets dropped silently"}


builder = StateGraph(State)
builder.add_node("n", node)
builder.add_edge(START, "n")
builder.add_edge("n", END)
graph = builder.compile()
