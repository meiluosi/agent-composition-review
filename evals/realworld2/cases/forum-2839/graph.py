"""Minimal structure for forum post forum-2839: interrupt in a function-invoked child.

    parent: START -> node_1 -> END
    node_1 calls: subgraph.invoke({"bar": ...}) in a loop

    child: START -> subgraph_node_1
           START -> subgraph_node_2   (calls interrupt)

The OP's shape, not the corrected forum answer: no checkpointer/thread_id, the
child is invoked as a function, and both child nodes start from START.

https://forum.langchain.com/t/2839
"""

from typing_extensions import TypedDict

from langgraph.graph import START, StateGraph
from langgraph.types import interrupt


class SubgraphState(TypedDict):
    bar: str
    node_2: str


def subgraph_node_1(state: SubgraphState) -> dict:
    return {"bar": "hi! " + state["bar"]}


def subgraph_node_2(state: SubgraphState) -> dict:
    user_answer = interrupt({"question": "continue"})
    # The OP also compares the prompt payload to the resume value here.
    if user_answer == "Y":
        return {"node_2": "approved"}
    return {"node_2": "rejected"}


subgraph_builder = StateGraph(SubgraphState)
subgraph_builder.add_node("subgraph_node_1", subgraph_node_1)
subgraph_builder.add_node("subgraph_node_2", subgraph_node_2)
subgraph_builder.add_edge(START, "subgraph_node_1")
subgraph_builder.add_edge(START, "subgraph_node_2")
subgraph = subgraph_builder.compile()


class State(TypedDict):
    foo: str


def call_subgraph(state: State) -> dict:
    subgraph_output = subgraph.invoke({"bar": f"{state['foo']}_1"})
    return {"foo": subgraph_output["bar"]}


builder = StateGraph(State)
builder.add_node("node_1", call_subgraph)
builder.add_edge(START, "node_1")
graph = builder.compile()


if __name__ == "__main__":
    print(graph.invoke({"foo": "bar"}))
