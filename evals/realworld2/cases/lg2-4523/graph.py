"""Minimal structure for langgraph#4523: update + resume through a subgraph.

    START --(route)--> tech_support (compiled child) -> respond -> END
                            |
                            +-- ask_problem_description -> parse_problem_description
                                                             (calls interrupt)

Resume: graph.invoke(Command(update={"message": "..."}, resume=True), config).
Expected: the resumed parse node sees the updated message.
Reported: it sees the original message; the update lands only after the node.

https://github.com/langchain-ai/langgraph/issues/4523
"""

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import Command, interrupt


class InputState(TypedDict):
    message: str


class GraphState(InputState):
    messages: Annotated[Sequence[BaseMessage], add_messages]


def ask_problem_description_node(state: GraphState) -> dict:
    return {"messages": [AIMessage(content="Please describe your problem")]}


def parse_problem_description_node(state: GraphState) -> dict:
    interrupt(state["messages"][-1].content)
    # The reported behavior: the Command(update=...) payload is not visible here.
    user_response = state.get("message")
    return {
        "messages": [
            HumanMessage(content=user_response),
            AIMessage(content="noted."),
        ],
    }


def build_child():
    child = StateGraph(GraphState, input_schema=InputState)
    child.add_node("ask_problem_description", ask_problem_description_node)
    child.add_node("parse_problem_description", parse_problem_description_node)
    child.set_entry_point("ask_problem_description")
    child.add_edge("ask_problem_description", "parse_problem_description")
    child.add_edge("parse_problem_description", END)
    return child.compile()


def route_by_intent(state: GraphState) -> str:
    return "tech_support"


def respond(state: GraphState) -> dict:
    return {"messages": [AIMessage(content="response")]}


workflow = StateGraph(GraphState, input_schema=InputState)
workflow.add_node("respond", respond)
workflow.add_node("tech_support", build_child())
workflow.add_conditional_edges(START, route_by_intent, ["tech_support"])
workflow.add_edge("tech_support", "respond")
workflow.add_edge("respond", END)
graph = workflow.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "111"}}
    graph.invoke({"message": "Hi, I've a technical issue"}, config)
    result = graph.invoke(
        Command(update={"message": "This is a problem description"}, resume=True),
        config,
    )
    print(result)
