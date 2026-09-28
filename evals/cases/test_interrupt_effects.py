"""Executable example of documented interrupt/replay behavior, not a reported bug."""

import unittest
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    operation_id: str
    done: bool


class LocalEffects:
    """In-process test double; never claims durable or distributed exactly-once."""
    def __init__(self):
        self.applied = []

    def apply(self, operation_id, idempotent=False):
        if not idempotent or operation_id not in self.applied:
            self.applied.append(operation_id)


def build_graph(effects, idempotent):
    # The operation is an already-authorized preparation step, followed by a
    # human review. It is NOT a payment awaiting approval.
    def prepare_and_review(state):
        effects.apply(state["operation_id"], idempotent=idempotent)
        interrupt("Preparation complete; continue review?")
        return {"done": True}
    builder = StateGraph(State)
    builder.add_node("prepare", prepare_and_review)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", END)
    return builder.compile(checkpointer=InMemorySaver())


class InterruptEffectTests(unittest.TestCase):
    def test_resume_reexecutes_the_pre_interrupt_effect(self):
        effects = LocalEffects()
        graph = build_graph(effects, False)
        config = {"configurable": {"thread_id": "replay"}}
        first = graph.invoke({"operation_id": "op-1", "done": False}, config)
        self.assertTrue(first["__interrupt__"])
        self.assertEqual(effects.applied, ["op-1"])
        final = graph.invoke(Command(resume=True), config)
        self.assertTrue(final["done"])
        self.assertEqual(effects.applied, ["op-1", "op-1"])

    def test_operation_identity_prevents_duplicate_effect_in_this_fixture(self):
        effects = LocalEffects()
        graph = build_graph(effects, True)
        for operation_id in ["op-1", "op-2"]:
            cfg = {"configurable": {"thread_id": operation_id}}
            graph.invoke({"operation_id": operation_id, "done": False}, cfg)
            result = graph.invoke(Command(resume=True), cfg)
            self.assertTrue(result["done"])
        self.assertEqual(effects.applied, ["op-1", "op-2"])

    def test_idempotency_does_not_suppress_new_operations(self):
        effects = LocalEffects()
        for key in ["same", "same", "new"]:
            effects.apply(key, idempotent=True)
        self.assertEqual(effects.applied, ["same", "new"])
