"""Minimal structure for langgraph#766: duplicated edge into a dict channel.

    START -> generate_new_extraction -> medical_mentor --> medical_mentor_pass -> END
                                                |                  ^
                                                +-- conditional ---+

medical_mentor has both a plain edge and a conditional edge to
medical_mentor_pass, and every node returns the complete ``medical`` dict.
The plain key receives two writes for the same super-step.

https://github.com/langchain-ai/langgraph/issues/766
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class GraphState(TypedDict):
    medical: dict


def generate_new_extraction(state: GraphState) -> dict:
    return {"medical": {**state["medical"], "generation": "draft", "iterations": 1}}


def medical_mentor(state: GraphState) -> dict:
    return {"medical": {**state["medical"], "feedback": "mentor", "correct": False}}


def medical_mentor_pass(state: GraphState) -> dict:
    return {"medical": {**state["medical"], "feedback": "done", "correct": True}}


def decide_to_finish(state: GraphState) -> str:
    return "medical_mentor_pass"


workflow = StateGraph(GraphState)
workflow.add_node("generate_new_extraction", generate_new_extraction)
workflow.add_node("medical_mentor", medical_mentor)
workflow.add_node("medical_mentor_pass", medical_mentor_pass)
workflow.set_entry_point("generate_new_extraction")
workflow.add_edge("generate_new_extraction", "medical_mentor")
# Same target registered twice: plain edge plus conditional edge.
workflow.add_edge("medical_mentor", "medical_mentor_pass")
workflow.add_edge("medical_mentor_pass", END)
workflow.add_conditional_edges(
    "medical_mentor",
    decide_to_finish,
    {
        "medical_mentor_pass": "medical_mentor_pass",
        "generate_new_extraction": "generate_new_extraction",
    },
)
app = workflow.compile()


if __name__ == "__main__":
    print(app.invoke({"medical": {}}))
