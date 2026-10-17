"""Minimal structure for langgraph#9086: tuple reducer across a checkpoint resume.

    START -> n -> END   (compiled with InMemorySaver)

The state key is declared as a tuple with ``operator.add``.  After the first
run the checkpointed value is restored as a list, so the next update tries to
add a tuple to a list and the thread cannot advance again.

https://github.com/langchain-ai/langgraph/issues/9086
"""

import operator
from typing import Annotated

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class State(TypedDict):
    items: Annotated[tuple[str, ...], operator.add]


def node(state: State) -> dict:
    return {"items": ("x",)}


graph = StateGraph(State)
graph.add_node("n", node)
graph.add_edge(START, "n")
graph.add_edge("n", END)
app = graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "t1"}}
    print("invoke 1 :", app.invoke({"items": ("a",)}, config))
    print("get_state:", app.get_state(config).values)
    print("invoke 2 :", app.invoke({"items": ("b",)}, config))
