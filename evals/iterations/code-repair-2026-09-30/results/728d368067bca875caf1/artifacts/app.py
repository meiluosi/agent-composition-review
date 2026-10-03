"""Reconcile a parent view with parallel analysis from a reusable subgraph."""
from typing import TypedDict
from langgraph.graph import START, END, StateGraph


class State(TypedDict):
    count: int
    label: str
    left: int
    right: int


class AnalysisOutput(TypedDict):
    left: int
    right: int


def build_graph(parent_gate=None, child_gate=None, calls=None):
    def record(name):
        if calls is not None:
            calls.append(name)

    def prepare(s):
        record('prepare')
        return {'count':s['count']+7}

    def refresh(s):
        record('refresh')
        if parent_gate is not None:
            parent_gate.wait()
        return {'label':s['label']+'|fresh'}

    def left(s):
        record('left')
        if parent_gate is not None:
            parent_gate.wait()
        if child_gate is not None:
            child_gate.wait()
        return {'left':s['left']+s['count']+len(s['label'])}

    def right(s):
        record('right')
        if child_gate is not None:
            child_gate.wait()
        return {'right':s['right']+s['count']-len(s['label'])}

    # Read the full snapshot, but publish only the analysis-owned fields.
    child=StateGraph(State, output_schema=AnalysisOutput)
    child.add_node('left',left)
    child.add_node('right',right)
    child.add_edge(START,'left')
    child.add_edge(START,'right')
    parent=StateGraph(State)
    parent.add_node('prepare',prepare)
    parent.add_node('refresh',refresh)
    parent.add_node('analysis',child.compile())
    parent.add_edge(START,'prepare')
    parent.add_edge('prepare','refresh')
    parent.add_edge('prepare','analysis')
    parent.add_edge('refresh',END)
    parent.add_edge('analysis',END)
    return parent.compile()
