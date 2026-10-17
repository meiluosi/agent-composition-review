"""ACR001 positive case: compiled subgraph shares a key with the parent.

The child graph is compiled without ``output_schema`` and both state schemas
declare ``b``.  The child's state is merged back into the parent, so the
subgraph node can collide with siblings.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    a: int
    b: int


class ChildState(TypedDict):
    b: int


def child_worker(state: ChildState) -> dict:
    return {"b": state["b"] + 1}


def parent_worker(state: ParentState) -> dict:
    return {"a": state["a"] + 1}


child = StateGraph(ChildState)
child.add_node("work", child_worker)
child.add_edge(START, "work")
child.add_edge("work", END)
child_graph = child.compile()

parent = StateGraph(ParentState)
parent.add_node("child", child_graph)
parent.add_node("work", parent_worker)
parent.add_edge(START, "child")
parent.add_edge("child", "work")
parent.add_edge("work", END)

graph = parent.compile()
