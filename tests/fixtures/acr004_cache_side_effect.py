"""ACR004 positive case: a cached node with a visible side effect."""

from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    b: int


@lru_cache(maxsize=128)
def cached_node(state: ParentState) -> dict:
    return {"b": state["b"] + 1}


builder = StateGraph(ParentState)
builder.add_node("cached", cached_node)
builder.add_edge(START, "cached")
builder.add_edge("cached", END)

graph = builder.compile()
