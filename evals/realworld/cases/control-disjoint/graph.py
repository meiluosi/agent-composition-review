"""Normal control: parallel branches write disjoint state keys.

Disjoint writes do not need a reducer and should not be reported.

Expected: acr scan reports no findings.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int
    y: int


def left(state: State) -> dict:
    return {"x": state["x"] + 1}


def right(state: State) -> dict:
    return {"y": state["y"] + 1}


builder = StateGraph(State)
builder.add_node("left", left)
builder.add_node("right", right)
builder.add_edge(START, "left")
builder.add_edge(START, "right")
builder.add_edge("left", END)
builder.add_edge("right", END)
graph = builder.compile()
