"""修复示例：子图声明 output_schema，只回传自己负责的字段。

与 broken.py 的唯一语义差别：

    - child = StateGraph(State)
    + child = StateGraph(State, output_schema=ChildOutput)

子图仍然读取父图的完整上下文（a、b），但只把 s1/s2 写回父图。
sibling 的 b 更新不再与子图冲突，并行结构保持不变。

运行：.venv/bin/python examples/langgraph-subgraph-leak/fixed.py
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int
    b: str
    s1: int
    s2: int


class ChildOutput(TypedDict):
    # 子图负责的字段边界：只声明它真正会写的键
    s1: int
    s2: int


def left(state: State) -> dict:
    return {"s1": state["s1"] + state["a"] + len(state["b"])}


def right(state: State) -> dict:
    return {"s2": state["s2"] + state["a"] - len(state["b"])}


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|updated"}


def build_fixed_graph():
    # 修复：声明输出 schema，子图输出从“完整快照”收窄为“自己负责的补丁”
    child = StateGraph(State, output_schema=ChildOutput)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")

    parent = StateGraph(State)
    parent.add_node("sibling", sibling)
    parent.add_node("child", child.compile())
    parent.add_edge(START, "sibling")
    parent.add_edge(START, "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()


if __name__ == "__main__":
    graph = build_fixed_graph()
    result = graph.invoke({"a": 1, "b": "old", "s1": 0, "s2": 0})
    print(result)
