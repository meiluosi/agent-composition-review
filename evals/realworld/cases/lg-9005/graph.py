"""Minimal structure for langgraph#9005: inherited field updated twice via Command.

    START -> node -> END

``State`` inherits ``count`` from ``BaseState`` and re-declares it (normal
Python inheritance).  The node returns the same update once as a plain state
object and once wrapped in ``Command(update=...)``; with an additive reducer
the two paths should agree.

https://github.com/langchain-ai/langgraph/issues/9005
"""

import operator
from dataclasses import dataclass
from typing import Annotated

from langgraph.graph import START, StateGraph
from langgraph.types import Command


@dataclass
class BaseState:
    count: Annotated[int, operator.add] = 0


@dataclass
class State(BaseState):
    # Overriding an inherited field is ordinary Python; the annotation repeats.
    count: Annotated[int, operator.add] = 0


def build(use_command: bool):
    def node(state: State):
        update = State(count=2)
        return Command(update=update) if use_command else update

    return (
        StateGraph(State)
        .add_node("node", node)
        .add_edge(START, "node")
        .compile()
    )


if __name__ == "__main__":
    print("direct: ", build(use_command=False).invoke(State(count=1)))
    print("Command:", build(use_command=True).invoke(State(count=1)))
