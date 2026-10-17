"""ACR005 positive case: a retried node with a visible side effect."""

from typing import TypeVar, TypedDict

from langgraph.graph import END, START, StateGraph

F = TypeVar("F")


def retry(times: int = 3):
    """Minimal retry decorator stand-in so the fixture has no extra dependency."""

    def decorator(func: F) -> F:
        return func

    return decorator


class ParentState(TypedDict):
    b: int


@retry(times=3)
def flaky_node(state: ParentState) -> dict:
    return {"b": state["b"] + 1}


builder = StateGraph(ParentState)
builder.add_node("flaky", flaky_node)
builder.add_edge(START, "flaky")
builder.add_edge("flaky", END)

graph = builder.compile()
