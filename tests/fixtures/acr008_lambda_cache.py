"""ACR008 positive: cache_policy on a lambda node."""

from typing import TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy


class S(TypedDict):
    out: str


shared = InMemoryCache()
graph = StateGraph(S)
graph.add_node("process", lambda state: {"out": "x"}, cache_policy=CachePolicy())
graph.add_edge(START, "process")
graph.add_edge("process", END)
graph.compile(cache=shared)
