"""Deterministic variant of the public reproduction; no model/API calls."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


class ChildOutput(TypedDict):
    """Only the fields this child owns may become parent updates."""

    s1: int
    s2: int


def default_a(state):
    return {"a": state["a"] + 10}


def default_b(state):
    return {"b": state["b"] + "|parent"}


def default_s1(state):
    return {"s1": state["s1"] + 1}


def default_s2(state):
    return {"s2": state["s2"] + 2}


def create_graph(
    *, node_a=default_a, node_b=default_b,
    sub_1=default_s1, sub_2=default_s2,
):
    child = StateGraph(State, output_schema=ChildOutput)
    child.add_node("subnode1", sub_1)
    child.add_node("subnode2", sub_2)
    child.add_edge(START, "subnode1")
    child.add_edge(START, "subnode2")

    parent = StateGraph(State)
    parent.add_node("nodea", node_a)
    parent.add_node("nodeb", node_b)
    parent.add_node("subgraph", child.compile())
    parent.add_edge(START, "nodea")
    parent.add_edge("nodea", "nodeb")
    parent.add_edge("nodea", "subgraph")
    parent.add_edge("nodeb", END)
    parent.add_edge("subgraph", END)
    return parent.compile()


if __name__ == "__main__":
    print(create_graph().invoke({"a": 123, "b": "old", "s1": 0, "s2": 0}))
