"""Minimal structure for langgraph#6290: sibling subgraphs sharing a reducer.

    subgraph_1 -> subgraph_2 --(is_end?)--> END
         ^                            |
         +----------- False ----------+

Both subgraphs are compiled with the same AgentState and added as parent
nodes, with no output schema.  subgraph_1 never writes g_retry_count, but its
echoed state is merged back into the parent, so the custom reducer runs again.

https://github.com/langchain-ai/langgraph/issues/6290
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph


def update_retry_count(count: int, is_increment: bool):
    if is_increment:
        return count + 1
    return count


class AgentState(TypedDict):
    g_retry_count: Annotated[int, update_retry_count]
    g_max_retry: int
    history: Annotated[list, operator.add]


def return_subgraph_1():
    def node_1(state: AgentState) -> dict:
        return {}

    wf = StateGraph(AgentState)
    wf.add_node("node_1", node_1)
    wf.set_entry_point("node_1")
    return wf.compile()


def return_subgraph_2():
    def node_1(state: AgentState) -> dict:
        return {"g_retry_count": True}

    wf = StateGraph(AgentState)
    wf.add_node("node_1", node_1)
    wf.set_entry_point("node_1")
    return wf.compile()


def is_end(state: AgentState) -> bool:
    return state["g_retry_count"] >= state["g_max_retry"]


def return_supergraph():
    wf = StateGraph(AgentState)
    wf.add_node("subgraph_1", return_subgraph_1())
    wf.add_node("subgraph_2", return_subgraph_2())
    wf.add_edge("subgraph_1", "subgraph_2")
    wf.add_conditional_edges(
        "subgraph_2", is_end, {True: END, False: "subgraph_1"}
    )
    wf.set_entry_point("subgraph_1")
    return wf.compile()


if __name__ == "__main__":
    for step in return_supergraph().stream(
        {"g_retry_count": 0, "g_max_retry": 3}, stream_mode="updates"
    ):
        print(step)
