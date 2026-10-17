"""Minimal structure for langgraph#9084: tags duplicated by nested subgraphs.

    depth 0: START -> leaf -> END
    depth n: wrap the previous compiled graph as a node, n times

The leaf counts how often the invoke-time tag "my-tag" is visible in
``config["tags"]``.  Expected 1 at every depth; reported behavior is 2^depth
because tags are concatenated again at each graph boundary.

https://github.com/langchain-ai/langgraph/issues/9084
"""

from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict


class State(TypedDict):
    copies: int


def leaf(state: State, config) -> dict:
    return {"copies": config["tags"].count("my-tag")}


def build(depth: int):
    builder = StateGraph(State)
    builder.add_node("leaf", leaf)
    builder.add_edge(START, "leaf")
    graph = builder.compile()
    for _ in range(depth):
        builder = StateGraph(State)
        builder.add_node("sub", graph)
        builder.add_edge(START, "sub")
        graph = builder.compile()
    return graph


if __name__ == "__main__":
    for depth in range(4):
        out = build(depth).invoke({"copies": 0}, {"tags": ["my-tag"]})
        print(f"subgraph depth {depth}: leaf sees 'my-tag' {out['copies']}x")
