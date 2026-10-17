"""Minimal structure for langgraph#9311: reducer hidden under a ReadOnly qualifier.

    sequential: START -> add_a -> add_b -> END
    parallel:   START -> add_a  and  START -> add_b  (both -> END)

Both writers update the same reducer key, declared as
``ReadOnly[Annotated[list[str], operator.add]]``.  The sequential graph is
expected to accumulate ["seed", "a", "b"]; the parallel graph is expected to
accept both writes through the reducer.

https://github.com/langchain-ai/langgraph/issues/9311
"""

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from typing_extensions import ReadOnly, TypedDict


class State(TypedDict):
    findings: ReadOnly[Annotated[list[str], operator.add]]


def add_a(state: State) -> dict:
    return {"findings": ["a"]}


def add_b(state: State) -> dict:
    return {"findings": ["b"]}


def build(parallel: bool):
    builder = StateGraph(State)
    builder.add_node("a", add_a)
    builder.add_node("b", add_b)
    builder.add_edge(START, "a")
    if parallel:
        builder.add_edge(START, "b")
        builder.add_edge("a", END)
    else:
        builder.add_edge("a", "b")
    builder.add_edge("b", END)
    return builder.compile()


if __name__ == "__main__":
    for parallel in (False, True):
        print(parallel, build(parallel).invoke({"findings": ["seed"]}))
