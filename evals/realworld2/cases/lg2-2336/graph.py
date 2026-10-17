"""Minimal structure for langgraph#2336: retry loop beside a parallel branch.

    START -> a --+-> b ---------> d -> END
                 |                ^
                 +-> c --retry--+ |
                     |            |
                     +--- next ---+

``c`` can loop while ``b`` has already moved on to ``d``.  Overlapping
super-steps end up writing the plain key ``count_b`` more than once.

https://github.com/langchain-ai/langgraph/issues/2336
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    count_a: int
    count_b: int
    count_ab: int
    count_b_true: bool
    count_ab_check: int


def pass_node(state: State) -> dict:
    return {}


def pass_node_b(state: State) -> dict:
    return {"count_a": state.get("count_a", 0) + 1}


def pass_node_c(state: State) -> dict:
    return {
        "count_b": state.get("count_b", 0) + 1,
        "count_ab_check": state.get("count_ab_check", 0) + 1,
        "count_b_true": False,
    }


def pass_node_c_condition(state: State) -> str:
    if not state["count_b_true"] and state["count_ab_check"] < 3:
        return "retry"
    return "next"


def pass_node_d(state: State) -> dict:
    return {"count_ab": state.get("count_a", 0) + state.get("count_b", 0)}


builder = StateGraph(State)
builder.add_node("a", pass_node)
builder.add_node("b", pass_node_b)
builder.add_node("c", pass_node_c)
builder.add_node("d", pass_node_d)
builder.add_edge(START, "a")
builder.add_edge("a", "b")
builder.add_edge("a", "c")
builder.add_edge("b", "d")
builder.add_conditional_edges(
    "c", pass_node_c_condition, {"retry": "c", "next": "d"}
)
builder.add_edge("d", END)
graph = builder.compile()


if __name__ == "__main__":
    print(graph.invoke({"count_a": 1}))
