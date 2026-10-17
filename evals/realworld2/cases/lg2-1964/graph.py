"""Minimal structure for langgraph#1964: parallel subgraphs feeding a sink.

    START -> question_generation --+-> child1 (subgraph) --+
                                    +-> child2 (subgraph) --+--> sink -> END

Both children use the full parent-shaped state and compile without an output
schema, so each returns the inherited ``question`` as part of its update patch.
The fan-in therefore sees two writes to the plain key ``question``.

https://github.com/langchain-ai/langgraph/issues/1964
"""

import operator
from functools import partial
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class ChildState(TypedDict):
    question: str
    results: Annotated[list[str], operator.add]


class ParentState(TypedDict):
    question: str
    results: Annotated[list[str], operator.add]


def child_node(state: ChildState, child_result_txt: str) -> dict:
    return {"results": [child_result_txt]}


def create_child(child_result_txt: str):
    child = StateGraph(ChildState)
    child.add_node("child", partial(child_node, child_result_txt=child_result_txt))
    child.add_edge(START, "child")
    child.add_edge("child", END)
    # No output_schema: the full ChildState, including the echoed question.
    return child.compile()


def question_generation(state: ParentState) -> dict:
    return {"question": "what's up?"}


def sink(state: ParentState) -> dict:
    return {"results": state["results"]}


parent = StateGraph(ParentState)
parent.add_node("question_generation", question_generation)
parent.add_node("child1", create_child("child 1 result"))
parent.add_node("child2", create_child("child 2 result"))
parent.add_node("sink", sink)
parent.add_edge(START, "question_generation")
parent.add_edge("question_generation", "child1")
parent.add_edge("question_generation", "child2")
parent.add_edge(["child1", "child2"], "sink")
parent.add_edge("sink", END)
parent_graph = parent.compile()


if __name__ == "__main__":
    print(parent_graph.invoke({"question": "hi", "results": []}))
