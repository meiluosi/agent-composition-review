"""ACR001 negative: a helper-returned child declares a narrow output_schema."""

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


def left(state: State) -> dict:
    return {"s1": state["a"]}


def right(state: State) -> dict:
    return {"s2": len(state["b"])}


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|sibling"}


def build_child():
    child = StateGraph(State, output_schema=ChildOutput)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")
    return child.compile()


def build_graph():
    parent = StateGraph(State)
    parent.add_node("sibling", sibling)
    parent.add_node("child", build_child())
    parent.add_edge(START, "sibling")
    parent.add_edge(START, "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()
