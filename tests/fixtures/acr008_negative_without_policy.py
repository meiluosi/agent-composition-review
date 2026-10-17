"""ACR008 negative: a lambda node without cache_policy is not flagged."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    out: str


graph = StateGraph(S)
graph.add_node("process", lambda state: {"out": "x"})
graph.add_edge(START, "process")
graph.add_edge("process", END)
graph.compile()
