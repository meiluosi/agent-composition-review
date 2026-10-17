"""Minimal structure for langgraph#9019: Overwrite via update_state on a plain key.

    START -> n -> END   (compiled with InMemorySaver)
    then: graph.update_state(cfg, {"x": Overwrite("replaced")})

``x`` is a plain (non-reducer) key.  Expected get_state().values to contain
"replaced"; reported behavior is that the Overwrite wrapper object itself is
persisted into the checkpoint.

https://github.com/langchain-ai/langgraph/issues/9019
"""

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite


class State(TypedDict):
    x: str


def node(state: State) -> dict:
    return {"x": "from_node"}


graph = StateGraph(State)
graph.add_node("n", node)
graph.add_edge(START, "n")
graph.add_edge("n", END)
app = graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "t1"}}
    app.invoke({"x": "init"}, config)
    app.update_state(config, {"x": Overwrite("replaced")})
    print(app.get_state(config).values)
