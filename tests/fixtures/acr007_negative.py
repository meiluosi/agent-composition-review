"""ACR007 negative: the node returns only declared state keys."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int
    y: str


def node(state: State) -> dict:
    return {"x": state["x"] + 1, "y": state["y"] + "!"}


builder = StateGraph(State)
builder.add_node("n", node)
builder.add_edge(START, "n")
builder.add_edge("n", END)
graph = builder.compile()
