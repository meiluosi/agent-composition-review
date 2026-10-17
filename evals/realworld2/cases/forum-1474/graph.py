"""Minimal structure for forum post forum-1474: parallel branches, one save node.

    START -> dispatch --+-> branch_alpha --+
                        +-> branch_beta  --+--> save_files -> END

Both branches write their own name into ``last_tool``; the list reducer makes
concurrent updates legal but loses branch identity/order by the time
save_files runs.  Without the reducer the parallel writes raise
InvalidUpdateError at key 'last_tool'.

https://forum.langchain.com/t/1474
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    last_tool: Annotated[list[str], operator.add]
    saved_output_dir: str


def dispatch(state: State) -> dict:
    return {}


def branch_alpha(state: State) -> dict:
    return {"last_tool": ["alpha_analysis"]}


def branch_beta(state: State) -> dict:
    return {"last_tool": ["beta_analysis"]}


def save_files(state: State) -> dict:
    # The reporter needs per-branch output dirs here, but only a merged list
    # of tool names reaches this node.
    return {"saved_output_dir": "clusters_created/" + ",".join(state["last_tool"])}


graph = StateGraph(State)
graph.add_node("dispatch", dispatch)
graph.add_node("branch_alpha", branch_alpha)
graph.add_node("branch_beta", branch_beta)
graph.add_node("save_files", save_files)
graph.add_edge(START, "dispatch")
graph.add_edge("dispatch", "branch_alpha")
graph.add_edge("dispatch", "branch_beta")
graph.add_edge(["branch_alpha", "branch_beta"], "save_files")
graph.add_edge("save_files", END)
compiled = graph.compile()


if __name__ == "__main__":
    print(compiled.invoke({"last_tool": [], "saved_output_dir": ""}))
