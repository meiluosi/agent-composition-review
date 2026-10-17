"""Minimal structure for langgraph#5980: cache + checkpointer interaction.

The callable is a module-level named function, so this is an *adjacent* cache
case, not ACR008's callable-identity failure. It checks that ACR008 stays quiet
when the node has a stable function identity.
"""

from typing import TypedDict

from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.types import CachePolicy


class State(TypedDict):
    x: int
    result: int


def expensive_node(state: State) -> dict:
    return {"result": state["x"] * 2}


graph = (
    StateGraph(State)
    .add_node(expensive_node, cache_policy=CachePolicy())
    .set_entry_point("expensive_node")
    .set_finish_point("expensive_node")
    .compile(cache=InMemoryCache(), checkpointer=InMemorySaver())
)
