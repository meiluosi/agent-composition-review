"""Minimal structure for langgraph#5362: message type across a Send boundary.

    START -> child (compiled subgraph) -> END

The router passes the parent messages to the child through a Send argument.
Expected: the child receives the concrete SystemMessage/HumanMessage.
Reported: it receives a plain BaseMessage.

https://github.com/langchain-ai/langgraph/issues/5362
"""

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import Send


class ParentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class SubgraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def child_node(state: SubgraphState) -> dict:
    first = state["messages"][0]
    # Reported: this is BaseMessage, not SystemMessage/HumanMessage.
    print("message class seen in subgraph:", type(first).__name__)
    return {"messages": [first]}


def build_child():
    child = StateGraph(SubgraphState)
    child.add_node("child_node", child_node)
    child.add_edge(START, "child_node")
    child.add_edge("child_node", END)
    return child.compile()


def router(state: ParentState) -> Send:
    return Send("child", {"messages": state["messages"]})


parent = StateGraph(ParentState)
parent.add_node("child", build_child())
parent.add_conditional_edges(START, router)
graph = parent.compile()


if __name__ == "__main__":
    print(graph.invoke({"messages": [SystemMessage("be helpful"), HumanMessage("test")]}))
