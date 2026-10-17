"""ACR006 positive case: a node that touches the checkpointer directly."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class ParentState(TypedDict):
    b: int


def peeking_node(state: ParentState) -> dict:
    graph.get_state({"configurable": {"thread_id": "demo"}})
    graph.update_state(
        {"configurable": {"thread_id": "demo"}}, {"b": state["b"] + 1}
    )
    return {}


builder = StateGraph(ParentState)
builder.add_node("peek", peeking_node)
builder.add_edge(START, "peek")
builder.add_edge("peek", END)

graph = builder.compile()
