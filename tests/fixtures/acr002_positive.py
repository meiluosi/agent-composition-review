"""ACR002 positive case: START fans out to two nodes writing ``b`` with no reducer."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    a: int
    b: int


def branch_one(state: ParentState) -> dict:
    return {"b": 1}


def branch_two(state: ParentState) -> dict:
    return {"b": 2}


builder = StateGraph(ParentState)
builder.add_node("one", branch_one)
builder.add_node("two", branch_two)
builder.add_edge(START, "one")
builder.add_edge(START, "two")
builder.add_edge("one", END)
builder.add_edge("two", END)

graph = builder.compile()
