"""Minimal structure for langgraph#7580: Overwrite + regular writes in one super-step.

The issue reproduces at channel level by updating one
``Annotated[int, operator.add]`` channel with ``[5, Overwrite(10), 3]``.
The graph below expresses the implied topology: three parallel branches that
write 5, Overwrite(10) and 3 to the same key in the same super-step.  The
regular value written after the Overwrite is silently dropped.

https://github.com/langchain-ai/langgraph/issues/7580
"""

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite
from typing_extensions import TypedDict


class State(TypedDict):
    total: Annotated[int, operator.add]


def write_five(state: State) -> dict:
    return {"total": 5}


def overwrite_ten(state: State) -> dict:
    return {"total": Overwrite(10)}


def write_three(state: State) -> dict:
    return {"total": 3}


graph = StateGraph(State)
graph.add_node("write_five", write_five)
graph.add_node("overwrite_ten", overwrite_ten)
graph.add_node("write_three", write_three)
graph.add_edge(START, "write_five")
graph.add_edge(START, "overwrite_ten")
graph.add_edge(START, "write_three")
graph.add_edge("write_five", END)
graph.add_edge("overwrite_ten", END)
graph.add_edge("write_three", END)
compiled = graph.compile()


if __name__ == "__main__":
    # Expected: an error, or 18 if all writes apply; reported: 10 (the 3 vanishes).
    print(compiled.invoke({"total": 0}))
