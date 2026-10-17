"""Normal control: two parallel nodes write the same key with a reducer.

The key uses Annotated[list[str], operator.add], so concurrent writes are
explicitly mergeable. This must not be reported as ACR002.

Expected: acr scan reports no findings.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    items: Annotated[list[str], operator.add]


def left(state: State) -> dict:
    return {"items": ["left"]}


def right(state: State) -> dict:
    return {"items": ["right"]}


builder = StateGraph(State)
builder.add_node("left", left)
builder.add_node("right", right)
builder.add_edge(START, "left")
builder.add_edge(START, "right")
builder.add_edge("left", END)
builder.add_edge("right", END)
graph = builder.compile()
