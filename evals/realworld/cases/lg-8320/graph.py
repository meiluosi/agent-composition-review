"""Minimal structure for langgraph#8320: a node returns an undeclared state key.

    START -> n -> END

``n`` returns both the declared ``x`` and an extra key.  The extra key is not
part of ``State``; the graph drops it silently instead of warning.

https://github.com/langchain-ai/langgraph/issues/8320
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    x: int


def node(state: State) -> dict:
    # "note" is not declared in State and is expected to be lost.
    return {"x": state["x"] + 1, "note": "not part of the state schema"}


graph = StateGraph(State)
graph.add_node("n", node)
graph.add_edge(START, "n")
graph.add_edge("n", END)
compiled = graph.compile()


if __name__ == "__main__":
    print(compiled.invoke({"x": 0}))
