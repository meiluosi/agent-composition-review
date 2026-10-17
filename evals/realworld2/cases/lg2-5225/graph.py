"""Minimal structure for langgraph#5225: declared default on a reducer field.

The issue shows only the state declaration: a pydantic field with
``Annotated[list[str], extend_list]`` and a default_factory of ["default"].
The surrounding graph is the trivial single-node case needed to run it.

Expected on invoke({}): the reducer starts from ["default"].
Reported: the channel starts empty (list()), so the default is lost.

https://github.com/langchain-ai/langgraph/issues/5225
"""

from typing import Annotated

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


def extend_list(original: list, new: list):
    original.extend(new)
    return original


class OverallState(BaseModel):
    variable: Annotated[list[str], extend_list] = Field(
        default_factory=lambda: ["default"]
    )


def node(state: OverallState) -> dict:
    return {"variable": ["node"]}


graph = StateGraph(OverallState)
graph.add_node("node", node)
graph.add_edge(START, "node")
graph.add_edge("node", END)
app = graph.compile()


if __name__ == "__main__":
    print(app.invoke({}))
