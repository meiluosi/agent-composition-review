"""Real-world shape: cache_policy on a bound method (langgraph-mcp / vanara).

A bound method has a stable module + qualname, so it is not ACR008's
callable-identity failure. The scanner should resolve ``nodes.run`` to the
class method and stay quiet.
"""

from typing import TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.types import CachePolicy


class State(TypedDict):
    x: int


class Nodes:
    def run(self, state: State) -> dict:
        return {"x": state["x"] + 1}


nodes = Nodes()
builder = StateGraph(State)
builder.add_node("run", nodes.run, cache_policy=CachePolicy())
builder.add_edge(START, "run")
builder.add_edge("run", END)
graph = builder.compile(cache=InMemoryCache())
