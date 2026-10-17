"""Minimal structure for langgraph#5062: Command.PARENT with update + Send list.

    parent: START -> supervisor (child graph) -> loader -> supervisor ...
    child : START -> tools (returns Command.PARENT) -> END

The handoff tool returns
    Command(update={"messages": [...]},
            goto=[Send("loader", ...)],
            graph=Command.PARENT)
The reported failure: the parent gets the Send but not the update.

https://github.com/langchain-ai/langgraph/issues/5062
"""

from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send


class AgentState(TypedDict):
    messages: list
    data_refs: list


handoff_calls = {"n": 0}


def handoff_tool(state: AgentState) -> Command:
    handoff_calls["n"] += 1
    tool_message = ToolMessage(
        content="task assigned to loader",
        tool_call_id="call_1",
        name="transfer_to_loader",
    )
    if handoff_calls["n"] > 1:
        # The parent loops loader -> supervisor; finish on the second pass so
        # one invocation demonstrates the handoff without looping forever.
        return Command(
            update={"messages": [AIMessage("handoff complete")]},
            goto=END,
            graph=Command.PARENT,
        )
    return Command(
        update={"messages": [AIMessage("transferring"), tool_message]},
        goto=[Send("loader", {"messages": [HumanMessage("load the data")]})],
        graph=Command.PARENT,
    )


def build_supervisor_agent():
    # In the original this is create_react_agent; its ToolNode combines the
    # tool's Command.PARENT output with the list of Send objects.
    child = StateGraph(AgentState)
    child.add_node("tools", handoff_tool)
    child.add_edge(START, "tools")
    child.add_edge("tools", END)
    return child.compile()


def loader(state: AgentState) -> dict:
    return {"messages": [AIMessage("loaded")]}


supervisor = StateGraph(AgentState)
supervisor.add_node("supervisor", build_supervisor_agent())
supervisor.add_node("loader", loader)
supervisor.add_edge(START, "supervisor")
supervisor.add_edge("loader", "supervisor")
graph = supervisor.compile()


if __name__ == "__main__":
    print(graph.invoke({"messages": [HumanMessage("读取 ./test.csv")], "data_refs": []}))
