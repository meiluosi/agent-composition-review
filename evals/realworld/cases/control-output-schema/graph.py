"""Normal control: a subgraph declares a narrow output_schema.

The child still reads the full parent state, but only commits s1/s2.
A parallel sibling writes b; no collision is possible because the child
does not return b.

Expected: acr scan reports no findings.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


class ChildOutput(TypedDict):
    s1: int
    s2: int


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|sibling"}


def left(state: State) -> dict:
    return {"s1": state["s1"] + state["a"]}


def right(state: State) -> dict:
    return {"s2": state["s2"] + len(state["b"])}


child = StateGraph(State, output_schema=ChildOutput)
child.add_node("left", left)
child.add_node("right", right)
child.add_edge(START, "left")
child.add_edge(START, "right")

parent = StateGraph(State)
parent.add_node("sibling", sibling)
parent.add_node("child", child.compile())
parent.add_edge(START, "sibling")
parent.add_edge(START, "child")
parent.add_edge("sibling", END)
parent.add_edge("child", END)
graph = parent.compile()
