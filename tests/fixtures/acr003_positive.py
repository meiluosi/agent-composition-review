"""ACR003 fallback case: subgraph without output_schema but no shared keys."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    a: int


class ChildState(TypedDict):
    child_only: int


def child_worker(state: ChildState) -> dict:
    return {"child_only": state["child_only"] + 1}


child = StateGraph(ChildState)
child.add_node("work", child_worker)
child.add_edge(START, "work")
child.add_edge("work", END)
child_graph = child.compile()

parent = StateGraph(ParentState)
parent.add_node("child", child_graph)
parent.add_edge(START, "child")
parent.add_edge("child", END)

graph = parent.compile()
