"""Minimal structure for langgraph#6446: parallel child subgraph sharing parent keys.

    START -> nodea --+-> nodeb  (writes b)
                     +-> child  (subgraph) -> sub_left  (writes s1)
                                           -> sub_right (writes s2)

The child is compiled without ``output_schema``, so its complete state --
including the inherited, unchanged ``b`` -- is merged back into the parent in
the same super-step in which ``nodeb`` writes ``b``.

https://github.com/langchain-ai/langgraph/issues/6446
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


def nodea(state: State) -> dict:
    return {"a": state["a"] + 1}


def nodeb(state: State) -> dict:
    return {"b": state["b"] + "|nodeb"}


def sub_left(state: State) -> dict:
    return {"s1": state["s1"] + 1}


def sub_right(state: State) -> dict:
    return {"s2": state["s2"] + 1}


def build_child():
    child = StateGraph(State)
    child.add_node("sub_left", sub_left)
    child.add_node("sub_right", sub_right)
    child.add_edge(START, "sub_left")
    child.add_edge(START, "sub_right")
    # No output_schema: the full State (a, b, s1, s2) is what flows back.
    return child.compile()


def build_graph():
    graph = StateGraph(State)
    graph.add_node("nodea", nodea)
    graph.add_node("nodeb", nodeb)
    graph.add_node("child", build_child())
    graph.add_edge(START, "nodea")
    graph.add_edge("nodea", "nodeb")
    graph.add_edge("nodea", "child")
    graph.add_edge("nodeb", END)
    graph.add_edge("child", END)
    return graph.compile()


if __name__ == "__main__":
    print(build_graph().invoke({"a": 1, "b": "seed", "s1": 0, "s2": 0}))
