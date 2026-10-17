"""ACR002 negative case: the parallel key has an ``operator.add`` reducer."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    a: int
    b: Annotated[int, operator.add]


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
