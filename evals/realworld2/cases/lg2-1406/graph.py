"""Minimal structure for langgraph#1406: Send fan-out into an un-reduced key.

    START -> generate_query --(Send x datas)--> num_pros -> num_pros1 -> END

Every num_pros task returns a string for ``num_p``; the key has no reducer and
is not declared on OverallState, so concurrent writes fail in one super-step.

https://github.com/langchain-ai/langgraph/issues/1406
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class OverallState(TypedDict):
    question: str
    answer: str
    user_datas: Annotated[list[str], operator.add]


class QueryOutputState(TypedDict):
    query: str
    datas: list[str]


class DataOutputState(TypedDict):
    num: str
    num_p: str


def generate_query(state: OverallState) -> dict:
    return {"query": state["question"][:2], "datas": ["1", "2", "3"]}


def fan_out(state: QueryOutputState) -> list[Send]:
    return [Send("num_pros", {"num": item}) for item in state["datas"]]


def num_pros(state: DataOutputState) -> dict:
    # One string per Send task, all landing on the same plain channel.
    return {"num_p": state["num"] + "123"}


def num_pros1(state: DataOutputState) -> dict:
    return {"query": state["num_p"] + "345"}


builder = StateGraph(OverallState)
builder.add_node("generate_query", generate_query)
builder.add_node("num_pros", num_pros)
builder.add_node("num_pros1", num_pros1)
builder.add_edge(START, "generate_query")
builder.add_conditional_edges("generate_query", fan_out, ["num_pros"])
builder.add_edge("num_pros", "num_pros1")
builder.add_edge("num_pros1", END)
graph = builder.compile()


if __name__ == "__main__":
    print(graph.invoke({"question": "foo", "answer": "", "user_datas": []}))
