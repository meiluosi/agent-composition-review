"""Minimal structure for forum post forum-2020: Command.PARENT handoff.

    parent: START -> agent_a (subgraph) ... -> agent_b
    agent_a: remember -> finish_tool -> (Command.PARENT, goto="agent_b")
    agent_b: START -> agent_b_node -> END

The handoff carries only {"messages": [...]} in Command.update.  Earlier
updates made inside agent_a in the same stream are not implicitly copied,
which is the reported surprise.

https://forum.langchain.com/t/2020
"""

from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command


class BaseAgenticState(MessagesState):
    elements: str | None


def remember(state: BaseAgenticState) -> dict:
    return {"messages": [AIMessage("intermediate result from agent_a")]}


def finish_tool(state: BaseAgenticState) -> Command:
    # Only this update crosses the parent boundary; state accumulated earlier
    # in the same subgraph stream is not copied.
    return Command(
        graph=Command.PARENT,
        goto="agent_b",
        update={"messages": [AIMessage("finished agent_a")]},
    )


def build_agent_a():
    graph = StateGraph(BaseAgenticState)
    graph.add_node("remember", remember)
    graph.add_node("finish_tool", finish_tool)
    graph.add_edge(START, "remember")
    graph.add_edge("remember", "finish_tool")
    graph.add_edge("finish_tool", END)
    return graph.compile()


def agent_b_node(state: BaseAgenticState) -> dict:
    return {"messages": [AIMessage("agent_b started")]}


parent = StateGraph(BaseAgenticState)
parent.add_node("agent_a", build_agent_a())
parent.add_node("agent_b", agent_b_node)
parent.add_edge(START, "agent_a")
parent.add_edge("agent_b", END)
graph = parent.compile()


if __name__ == "__main__":
    print(
        graph.invoke({"messages": [HumanMessage("do the task")], "elements": None})
    )
