"""ACR009 positive: a class-based node with retry_policy invokes two subgraphs."""

from typing import TypedDict

from langgraph.graph import START, StateGraph
from langgraph.types import RetryPolicy


class State(TypedDict):
    text: str


def make_subgraph(text: str):
    return (
        StateGraph(State)
        .add_node("n", lambda state: {"text": text})
        .add_edge(START, "n")
        .compile()
    )


research = make_subgraph("RESEARCH")
review = make_subgraph("REVIEW")


class ResearchThenReview:
    def __call__(self, state: State) -> dict:
        research.invoke({})
        return review.invoke({})


graph = (
    StateGraph(State)
    .add_node("work", ResearchThenReview(), retry_policy=RetryPolicy())
    .add_edge(START, "work")
    .compile()
)
