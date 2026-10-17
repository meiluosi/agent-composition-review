"""Minimal structure for Stack Overflow question 79725144: mutating reducer loop.

    START -> n1 --(b >= threshold?)--> END
              ^       |
              +-- no -+

State is a pydantic model with a list reducer.  ``append_to_list`` mutates its
input list and returns it, so the same items can be appended again when the
reducer runs on a later conditional-edge step, doubling every Fibonacci value.

https://stackoverflow.com/questions/79725144
"""

from collections.abc import Iterable
from typing import Annotated, Any

from langchain_core.runnables import Runnable
from langgraph.constants import END
from langgraph.graph import StateGraph
from pydantic import BaseModel


def append_to_list(a: list, b) -> list:
    print(f"append_to_list: {a}, {b}")
    # Mutates the channel value in place; the OP's follow-up answer calls this
    # the cause of the duplicated entries.
    if isinstance(b, Iterable) and not isinstance(b, str):
        a.extend(b)
    else:
        a.append(b)
    return a


class Node(Runnable):
    def __init__(self, name: str):
        self.name = name

    def invoke(self, input: Any, config: dict | None = None) -> Any:
        c = input.a + input.b
        print(f"{self.name}: {input}")
        return {"a": input.b, "b": c, "fibonacci_list": c}


class ConditionalNode(Runnable):
    def __init__(self, name: str, threshold: int):
        self.threshold = threshold
        self.name = name

    def invoke(self, input: Any, config) -> Any:
        print(f"{self.name}: {input}")
        # The post returns {"path": ...}; the branch predicate must yield the
        # route key itself, so the fixture returns the bool used as the key.
        return input.b >= self.threshold


class State(BaseModel):
    a: int
    b: int
    fibonacci_list: Annotated[list[int], append_to_list]


graph = StateGraph(State)
graph.add_node("n1", Node(name="n1"))
graph.add_conditional_edges(
    "n1",
    ConditionalNode(name="CN", threshold=10),
    {True: END, False: "n1"},
)
graph.set_entry_point("n1")
g = graph.compile()


if __name__ == "__main__":
    print(g.invoke({"a": 1, "b": 1, "fibonacci_list": [1, 1]}))
