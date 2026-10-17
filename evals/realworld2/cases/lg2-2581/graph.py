"""Minimal structure for langgraph#2581: Send fan-out into a compiled subgraph.

    START -> parent_node_1 --[Send x fathers]--> child_node (subgraph)
                                                     |
                                                     v
                                              parent_node_2 -> END

The child subgraph declares only ``father`` but its node returns ``kids``, so
the subgraph boundary rejects the write.

https://github.com/langchain-ai/langgraph/issues/2581
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class SubgraphState(TypedDict):
    father: str


def get_kids(state: SubgraphState) -> dict:
    # "kids" is not declared in SubgraphState.
    return {"kids": [state["father"] + " Junior"]}


child_builder = StateGraph(SubgraphState)
child_builder.add_node("get_kids", get_kids)
child_builder.add_edge(START, "get_kids")
child_builder.add_edge("get_kids", END)
child = child_builder.compile()


class ParentState(TypedDict):
    fathers: list[str]
    kids: Annotated[list[str], operator.add]


def parent_node_1(state: ParentState) -> dict:
    return {}


def fan_out(state: ParentState) -> list[Send]:
    return [Send("child_node", {"father": father}) for father in state["fathers"]]


def parent_node_2(state: ParentState) -> dict:
    return {}


builder = StateGraph(ParentState)
builder.add_node("parent_node_1", parent_node_1)
builder.add_node("child_node", child)
builder.add_node("parent_node_2", parent_node_2)
builder.add_edge(START, "parent_node_1")
builder.add_conditional_edges("parent_node_1", fan_out, ["child_node"])
builder.add_edge("child_node", "parent_node_2")
builder.add_edge("parent_node_2", END)
graph = builder.compile()


if __name__ == "__main__":
    print(graph.invoke({"fathers": ["Jon", "Matt"], "kids": []}))
