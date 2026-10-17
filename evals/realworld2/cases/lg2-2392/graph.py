"""Minimal structure for langgraph#2392: private child state, no parent mapping.

    level 1: START -> code_feedback_agent (compiled level-2 graph) -> END
    level 2: START -> last_node -> END        (state = CodeFeedbackPrivateState)

The level-2 graph writes only a private key.  Used as a node of the level-1
graph, whose state is OverallState, that update cannot land on any parent key.

https://github.com/langchain-ai/langgraph/issues/2392
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class OverallState(TypedDict):
    question: str
    answer: str


class CodeFeedbackPrivateState(BaseModel):
    debugging_result: str = Field(default="")


def last_node(state: CodeFeedbackPrivateState) -> dict:
    return {"debugging_result": state.debugging_result}


level2 = StateGraph(CodeFeedbackPrivateState)
level2.add_node("last_node", last_node)
level2.add_edge(START, "last_node")
level2.add_edge("last_node", END)
level2_graph = level2.compile()


level1 = StateGraph(OverallState)
level1.add_node("code_feedback_agent", level2_graph)
level1.add_edge(START, "code_feedback_agent")
level1.add_edge("code_feedback_agent", END)
level1_graph = level1.compile()


if __name__ == "__main__":
    print(level1_graph.invoke({"question": "why?", "answer": ""}))
