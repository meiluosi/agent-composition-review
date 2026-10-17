"""ACR007 positive: a class-based node returns a key outside the state schema.

This mirrors the shape of LangGraph issue #2644: ``node1()`` is registered as a
node and returns ``{"zoo": ...}`` although the graph state only declares
``foo``. LangGraph filters ``zoo`` and raises "Must write to at least one of
['foo']".
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    foo: int


class TmpState(TypedDict):
    zoo: int


class node1:
    def __call__(self, state: State) -> TmpState:
        return {"zoo": state["foo"] + 1}


class node2:
    def __call__(self, state: TmpState) -> State:
        return {"foo": state["zoo"] + 1}


builder = StateGraph(State)
builder.add_node("node_1", node1())
builder.add_node("node_2", node2())
builder.add_edge(START, "node_1")
builder.add_edge("node_1", "node_2")
builder.add_edge("node_2", END)
graph = builder.compile()
