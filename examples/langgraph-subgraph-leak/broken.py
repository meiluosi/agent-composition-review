"""故障示例：父图与子图共享状态键，子图把完整状态当补丁回传。

结构（复现自 LangGraph #6446 的父/子图扇出）：

    START ──┬─> sibling  (写 b)
            └─> child    (子图) ──┬─> left  (写 s1)
                                 └─> right (写 s2)

子图用 StateGraph(State) 构建、没有声明 output_schema，因此默认回传整份
State——其中包括自己从未写过的旧 b。父图把这份完整状态当作更新补丁，
与并发 sibling 写入的新 b 在同一 superstep 合并，触发 InvalidUpdateError。

运行：.venv/bin/python examples/langgraph-subgraph-leak/broken.py
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    a: int   # 父图提供的只读上下文
    b: str   # sibling 负责写
    s1: int  # 子图 left 负责写
    s2: int  # 子图 right 负责写


def left(state: State) -> dict:
    # 读取父图的 a、b（完整上下文），但只写自己负责的 s1
    return {"s1": state["s1"] + state["a"] + len(state["b"])}


def right(state: State) -> dict:
    return {"s2": state["s2"] + state["a"] - len(state["b"])}


def sibling(state: State) -> dict:
    return {"b": state["b"] + "|updated"}


def build_broken_graph():
    # 问题根源：没有 output_schema，子图输出 = 完整 State（含旧 b）
    child = StateGraph(State)
    child.add_node("left", left)
    child.add_node("right", right)
    child.add_edge(START, "left")
    child.add_edge(START, "right")

    parent = StateGraph(State)
    parent.add_node("sibling", sibling)
    parent.add_node("child", child.compile())
    # 从 START 同时扇出，sibling 与子图在不同分支并发执行
    parent.add_edge(START, "sibling")
    parent.add_edge(START, "child")
    parent.add_edge("sibling", END)
    parent.add_edge("child", END)
    return parent.compile()


if __name__ == "__main__":
    graph = build_broken_graph()
    graph.invoke({"a": 1, "b": "old", "s1": 0, "s2": 0})
