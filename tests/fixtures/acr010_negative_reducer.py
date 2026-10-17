"""ACR010 negative: the retry loop writes a reducer-backed key."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    count_b: Annotated[int, operator.add]


def c(state: State) -> dict:
    return {"count_b": 1}


def route_c(state: State) -> str:
    return "retry" if state["count_b"] < 2 else "next"


builder = StateGraph(State)
builder.add_node("c", c)
builder.add_edge(START, "c")
builder.add_conditional_edges("c", route_c, {"retry": "c", "next": END})
graph = builder.compile()
