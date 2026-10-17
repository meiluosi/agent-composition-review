"""ACR002 negative: the reducer is nested inside a ReadOnly qualifier.

``ReadOnly[Annotated[list[str], operator.add]]`` still declares a reducer;
the scanner must unwrap the qualifier before deciding that the key has none.
"""

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from typing_extensions import ReadOnly, TypedDict


class State(TypedDict):
    findings: ReadOnly[Annotated[list[str], operator.add]]


def left(state: State) -> dict:
    return {"findings": ["left"]}


def right(state: State) -> dict:
    return {"findings": ["right"]}


builder = StateGraph(State)
builder.add_node("left", left)
builder.add_node("right", right)
builder.add_edge(START, "left")
builder.add_edge(START, "right")
builder.add_edge("left", END)
builder.add_edge("right", END)
graph = builder.compile()
