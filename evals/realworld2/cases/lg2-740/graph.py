"""Minimal structure for langgraph#740: node writes no declared state key.

    START -> execute_step -> END

The issue's plan-and-execute state is reduced to the four keys named in the
error.  ``execute_step`` returns no update, which LangGraph rejects; a
TypedDict default on ``current_index`` would not help because Python ignores
TypedDict defaults (the thread's maintainer explanation).

https://github.com/langchain-ai/langgraph/issues/740
"""

import operator
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph


class GraphState(TypedDict, total=False):
    input: str
    plan: str
    past_steps: Annotated[list[tuple[str, str]], operator.add]
    response: str
    # Not a real default: TypedDict ignores class-body values at runtime.
    current_index: Optional[int] = 0


def execute_step(state: GraphState) -> dict:
    # The node completes without writing any declared state key.
    return {}


workflow = StateGraph(GraphState)
workflow.add_node("gcf", execute_step)
workflow.set_entry_point("gcf")
workflow.add_edge("gcf", END)
graph = workflow.compile()


if __name__ == "__main__":
    print(graph.invoke({"input": "do it", "plan": "step", "past_steps": []}))
