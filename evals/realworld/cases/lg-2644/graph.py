"""Minimal structure for langgraph#2644: nodes returning a foreign schema.

    START -> node_1 -> node_2 -> END

The graph state declares only 'foo'.  ``NodeOne`` is annotated to return
``TmpState`` (key 'zoo') and writes only that key, so the graph cannot update
any declared state key and rejects the step.

https://github.com/langchain-ai/langgraph/issues/2644
"""

from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    foo: int


class TmpState(TypedDict):
    zoo: int


class NodeOne:
    def __call__(self, state: State) -> TmpState:
        return {"zoo": state["foo"] + 1}


class NodeTwo:
    def __call__(self, state: TmpState) -> State:
        return {"foo": state["zoo"] + 1}


graph = StateGraph(State)
graph.add_node("node_1", NodeOne())
graph.add_node("node_2", NodeTwo())
graph.add_edge(START, "node_1")
graph.add_edge("node_1", "node_2")
graph.add_edge("node_2", END)
compiled = graph.compile()


if __name__ == "__main__":
    print(compiled.invoke({"foo": 1}))
