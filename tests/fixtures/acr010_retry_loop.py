"""ACR010 positive: retry self-loop writes a non-reducer key.

Mirrors the shape of issue #2336: ``c`` has a conditional edge back to itself
and returns ``count_b`` while a parallel branch is in flight. The schema has no
reducer for ``count_b``.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    count_a: int
    count_b: int
    retry: bool


def a(state: State) -> dict:
    return {}


def b(state: State) -> dict:
    return {"count_a": state.get("count_a", 0) + 1}


def c(state: State) -> dict:
    return {"count_b": state.get("count_b", 0) + 1, "retry": False}


def route_c(state: State) -> str:
    return "retry" if not state.get("retry") else "next"


builder = StateGraph(State)
builder.add_node("a", a)
builder.add_node("b", b)
builder.add_node("c", c)
builder.add_edge(START, "a")
builder.add_edge("a", "b")
builder.add_edge("a", "c")
builder.add_conditional_edges("c", route_c, {"retry": "c", "next": END})
graph = builder.compile()
