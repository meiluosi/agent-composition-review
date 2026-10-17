"""Minimal structure for langgraph#8608: error_handler with a parallel sibling.

    solo:     START -> boom -> END
    parallel: START -> boom  and  START -> sibling  (same super-step)

``boom`` raises; ``error_handler`` catches it and returns a Command update.
Expected: the graph finishes with x="handled" in every configuration.
Reported: the original RuntimeError is re-raised whenever another node shares
the super-step (and for several streaming configurations).

Requires a langgraph version with the ``error_handler`` node parameter (the
issue used 1.2.11; the repository's pinned 1.0.3 predates it).

https://github.com/langchain-ai/langgraph/issues/8608
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command


class State(TypedDict, total=False):
    x: str
    y: str


def boom(state: State) -> dict:
    raise RuntimeError("test")


def sibling(state: State) -> dict:
    return {"y": "sibling"}


def handler(state: State, error) -> Command:
    return Command(update={"x": "handled"}, goto=END)


def build(parallel: bool):
    builder = (
        StateGraph(State)
        .add_node("boom", boom, error_handler=handler)
        .add_edge(START, "boom")
    )
    if parallel:
        builder.add_node("sibling", sibling)
        builder.add_edge(START, "sibling")
    return builder.compile()


if __name__ == "__main__":
    print(build(parallel=True).invoke({}))
