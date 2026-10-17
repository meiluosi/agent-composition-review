"""Minimal structure for langgraph#4879: RemoteGraph with interrupt.

Deployment 1 (served by ``langgraph dev``, remote agent-1):
    START -> node_a -> node_b(interrupt) -> END

Deployment 2 (supervisor):
    START -> child (RemoteGraph of agent-1) -> END

Expected: Command(resume=...) on the supervisor resumes node_b in the remote
thread.  Reported: the remote graph restarts and re-asks the interrupt.

https://github.com/langchain-ai/langgraph/issues/4879
"""

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.pregel.remote import RemoteGraph
from langgraph.types import Command, interrupt

REMOTE_URL = "http://127.0.0.1:2024/"


def build_remote_agent():
    """The graph exposed by ``langgraph.json`` as agent-1."""

    def node_a(state: MessagesState) -> dict:
        return {"messages": [AIMessage("hello")]}

    def node_b(state: MessagesState) -> Command:
        feedback = interrupt("what is your name?")
        return Command(
            update={"messages": [AIMessage(f"hello {feedback}")]},
            goto=END,
        )

    graph = StateGraph(MessagesState)
    graph.add_node("node_a", node_a)
    graph.add_node("node_b", node_b)
    graph.add_edge(START, "node_a")
    graph.add_edge("node_a", "node_b")
    graph.add_edge("node_b", END)
    return graph.compile()


def build_supervisor():
    # RemoteGraph talks to the graph above over the dev server; no local import.
    remote_graph = RemoteGraph("agent-1", url=REMOTE_URL)
    graph = StateGraph(MessagesState)
    graph.add_node("child", remote_graph)
    graph.add_edge(START, "child")
    return graph.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    # Requires `langgraph dev` serving build_remote_agent() at REMOTE_URL.
    print(build_supervisor())
