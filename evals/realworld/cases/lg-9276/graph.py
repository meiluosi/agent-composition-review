"""Minimal structure for langgraph#9276: completed sibling re-run on subgraph resume.

    flat:    START -> gate  (interrupts or fails)
             START -> agent (completed in the same super-step)
    nested:  START -> block (compiled subgraph containing gate + agent)

On resume the flat graph reuses the agent's completed write, while the nested
version runs the agent again.  The model call here is replaced by a counter so
the duplicate side effect is visible without an external model.

https://github.com/langchain-ai/langgraph/issues/9276
"""

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict, total=False):
    text: str
    approved: int
    answer: str


def run(nested: bool, stop: str) -> None:
    model_calls: list[int] = []
    attempts: list[int] = []

    def gate(state: State) -> dict:
        if stop == "interrupt":
            return {"approved": interrupt("approve?")}
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
        return {"approved": 1}

    def agent(state: State) -> dict:
        # Stand-in for a chat-model call: the side effect must happen once.
        model_calls.append(1)
        return {"answer": f"reply {len(model_calls)}"}

    block = StateGraph(State)
    block.add_node("gate", gate)
    block.add_node("agent", agent)
    block.add_edge(START, "gate")
    block.add_edge(START, "agent")

    if nested:
        outer = StateGraph(State)
        outer.add_node("block", block.compile())
        outer.add_edge(START, "block")
        graph = outer.compile(checkpointer=InMemorySaver())
    else:
        graph = block.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "1"}}
    if stop == "interrupt":
        paused = graph.invoke({"text": "hello"}, config)
        final = graph.invoke(
            Command(resume={paused["__interrupt__"][0].id: 1}), config
        )
    else:
        try:
            graph.invoke({"text": "hello"}, config)
        except RuntimeError:
            pass
        final = graph.invoke(None, config)

    print(
        f"{stop:9} nested={nested!s:5} "
        f"model calls: {len(model_calls)} answer: {final['answer']!r}"
    )


if __name__ == "__main__":
    for stop in ("interrupt", "failure"):
        for nested in (False, True):
            run(nested, stop)
