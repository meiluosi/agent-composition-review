"""Minimal structure for langgraph#1909: Send-activated node writes a private key.

    START --(Send per item)--> node_activate_by_Send -> END

The node is annotated to return PrivateState, but its keys are not declared on
the graph's State.  Send only supplies the node input; it does not register
the private output schema on the graph.

https://github.com/langchain-ai/langgraph/issues/1909
"""

import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send


class PrivateState(BaseModel):
    private_var: Annotated[list[str], operator.add]


class State(TypedDict):
    list_of_vars: list[str]


def router(state: State) -> list[Send]:
    return [
        Send("node_activate_by_Send", {"var": var})
        for var in state["list_of_vars"]
    ]


def node_activate_by_Send(state: dict) -> dict:
    # PrivateState is a return annotation only; private_var is not on State.
    return {"private_var": [state["var"]]}


graph = StateGraph(State)
graph.add_conditional_edges(START, router)
graph.add_node("node_activate_by_Send", node_activate_by_Send)
graph.add_edge("node_activate_by_Send", END)
app = graph.compile()


if __name__ == "__main__":
    print(app.invoke({"list_of_vars": ["a", "b", "c"]}))
