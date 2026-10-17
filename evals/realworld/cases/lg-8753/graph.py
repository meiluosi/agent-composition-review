"""Minimal structure for langgraph#8753: shared cache across two graphs.

    build(HandlerA): START -> process -> END
    build(HandlerB): START -> process -> END

Both graphs name their node "process" and share one InMemoryCache.  The two
handlers are callable objects, which have no module/qualname identity, so the
cache namespace degenerates to "__dynamic__" and the second graph receives the
first graph's cached result.

https://github.com/langchain-ai/langgraph/issues/8753
"""

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


if __name__ == "__main__":
    print(build(HandlerA()).invoke({"out": "x"}))
    # Expected "from graph B"; reported behavior is a cached "from graph A".
    print(build(HandlerB()).invoke({"out": "x"}))
