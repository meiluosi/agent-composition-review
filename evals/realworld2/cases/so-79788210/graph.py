"""Minimal structure for Stack Overflow question 79788210: subgraph node + routing.

    START --(route_to_workflow)--> primary_assistant
                                   generate_query_or_respond
                                   scheduler_assistant (compiled subgraph)
    enter_scheduler_assistant -> scheduler_assistant

The reported failure is KeyError: 'scheduler_assistant' inside LangGraph's
routing while the compiled subgraph is registered as a parent node.  The
question gives only this minimal structure, so the fixture mirrors it; it is
not guaranteed to reproduce the KeyError.

https://stackoverflow.com/questions/79788210
"""

from typing import TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph


class SharedState(MessagesState):
    dialog_state: str


def scheduler_node(state: SharedState) -> dict:
    return {"messages": [AIMessage("scheduling")]}


def build_scheduler_subgraph():
    subgraph = StateGraph(SharedState)
    subgraph.add_node("scheduler", scheduler_node)
    subgraph.add_edge(START, "scheduler")
    subgraph.add_edge("scheduler", END)
    return subgraph.compile()


def create_entry_node(assistant_name: str, new_dialog_state: str):
    def entry_node(state: SharedState) -> dict:
        tool_call_id = state["messages"][-1].tool_calls[0]["id"]
        return {
            "messages": [
                ToolMessage(
                    name=new_dialog_state,
                    content=f"The assistant is now the {assistant_name}.",
                    tool_call_id=tool_call_id,
                )
            ],
            "dialog_state": new_dialog_state,
        }

    return entry_node


def route_to_workflow(state: SharedState) -> str:
    return "scheduler_assistant"


def primary_assistant(state: SharedState) -> dict:
    return {"messages": [AIMessage("primary assistant")]}


def generate_query_or_respond(state: SharedState) -> dict:
    return {"messages": [AIMessage("query or respond")]}


builder = StateGraph(SharedState)
builder.add_node("scheduler_assistant", build_scheduler_subgraph())
builder.add_node(
    "enter_scheduler_assistant",
    create_entry_node("Scheduler Assistant", "scheduler_assistant"),
)
builder.add_node("primary_assistant", primary_assistant)
builder.add_node("generate_query_or_respond", generate_query_or_respond)
builder.add_edge("enter_scheduler_assistant", "scheduler_assistant")
builder.add_conditional_edges(
    START,
    route_to_workflow,
    [
        "primary_assistant",
        "scheduler_assistant",
        "generate_query_or_respond",
    ],
)
graph = builder.compile()


if __name__ == "__main__":
    print(graph.invoke({"messages": [], "dialog_state": ""}))
