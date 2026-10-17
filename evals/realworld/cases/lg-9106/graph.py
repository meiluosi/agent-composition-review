"""Minimal structure for langgraph#9106: parallel interrupts inside a subgraph.

    parent: START -> child -> END       (compiled with InMemorySaver)
    child:  START -> approve_a  (interrupt, then side effect)
            START -> approve_b  (interrupt, then side effect)

The two interrupts are resumed one at a time, each by its own interrupt id.
Expected: each side effect runs once.  Reported: the sibling that completed
first runs again on the second resume, because a nested graph treats every run
as replaying.

https://github.com/langchain-ai/langgraph/issues/9106
"""

import operator
from collections import Counter
from typing import Annotated

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

# Counts how often each node got past interrupt(), i.e. performed its effect.
runs: Counter[str] = Counter()


class State(TypedDict):
    log: Annotated[list[str], operator.add]


def approve_a(state: State) -> dict:
    interrupt("approve a?")
    runs["a"] += 1  # stands in for an external write
    return {"log": ["a"]}


def approve_b(state: State) -> dict:
    interrupt("approve b?")
    runs["b"] += 1
    return {"log": ["b"]}


def build():
    child = (
        StateGraph(State)
        .add_node("approve_a", approve_a)
        .add_node("approve_b", approve_b)
        .add_edge(START, "approve_a")
        .add_edge(START, "approve_b")
        .compile()
    )
    return (
        StateGraph(State)
        .add_node("child", child)
        .add_edge(START, "child")
        .compile(checkpointer=InMemorySaver())
    )


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "1"}}
    app = build()
    result = app.invoke({"log": []}, config)
    while pending := result.get("__interrupt__"):
        result = app.invoke(Command(resume={pending[0].id: "yes"}), config)
    print("log: ", result["log"])
    print("runs:", dict(runs))
    assert runs == {"a": 1, "b": 1}, f"a completed sibling ran again: {dict(runs)}"
