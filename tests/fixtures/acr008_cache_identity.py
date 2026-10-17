"""ACR008 positive: cache_policy on a callable object with no stable identity."""

from typing import TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy


class S(TypedDict):
    out: str


shared = InMemoryCache()


class HandlerA:
    def __call__(self, state: S) -> dict:
        return {"out": "from graph A"}


class HandlerB:
    def __call__(self, state: S) -> dict:
        return {"out": "from graph B"}


def build(handler) -> object:
    graph = StateGraph(S)
    graph.add_node("process", handler, cache_policy=CachePolicy())
    graph.add_edge(START, "process")
    graph.add_edge("process", END)
    return graph.compile(cache=shared)
