"""ACR008 negative: cache_policy on a module-level named function."""

from typing import TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy


class S(TypedDict):
    out: str


cache = InMemoryCache()


def process(state: S) -> dict:
    return {"out": "pure computation"}


graph = (
    StateGraph(S)
    .add_node("process", process, cache_policy=CachePolicy())
    .add_edge(START, "process")
    .add_edge("process", END)
    .compile(cache=cache)
)
