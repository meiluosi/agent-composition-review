"""Read-miss mutation described in LangGraph issue #8754.

The guarded subclass is a scoped local workaround, not an upstream patch.
"""

import asyncio
from copy import deepcopy
import unittest
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class GuardedSaver(InMemorySaver):
    def get_tuple(self, config):
        options = config["configurable"]
        namespaces = self.storage.get(options["thread_id"])
        if namespaces is None or options.get("checkpoint_ns", "") not in namespaces:
            return None
        return super().get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        if config is not None and config["configurable"]["thread_id"] not in self.storage:
            return
        yield from super().list(config, filter=filter, before=before, limit=limit)


def snapshot(saver):
    return deepcopy((dict(saver.storage), dict(saver.writes), dict(saver.blobs)))


def config(thread, namespace="", checkpoint_id=None):
    options = dict(thread_id=thread, checkpoint_ns=namespace)
    if checkpoint_id is not None:
        options["checkpoint_id"] = checkpoint_id
    return {"configurable": options}


def populate(saver):
    class State(TypedDict):
        count: int
    graph = StateGraph(State)
    graph.add_node("increment", lambda s: {"count": s["count"] + 1})
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    compiled = graph.compile(checkpointer=saver)
    compiled.invoke({"count": 1}, config("existing"))
    return compiled


class CheckpointReadTests(unittest.TestCase):
    def test_reported_read_misses_create_ghost_threads(self):
        saver = InMemorySaver()
        for index in range(20):
            self.assertIsNone(saver.get_tuple(config(f"missing-{index}")))
        self.assertEqual(len(saver.storage), 20)

    def test_guarded_sync_misses_preserve_all_storage(self):
        saver = GuardedSaver()
        populate(saver)
        before = snapshot(saver)
        for cfg in [config("missing"), config("missing-id", checkpoint_id="absent"),
                    config("existing", "missing-namespace"),
                    config("existing", checkpoint_id="missing-checkpoint")]:
            self.assertIsNone(saver.get_tuple(cfg))
            self.assertEqual(list(saver.list(cfg)), [])
        self.assertEqual(snapshot(saver), before)

    def test_guarded_async_misses_preserve_all_storage(self):
        saver = GuardedSaver()
        before = snapshot(saver)
        async def run():
            for cfg in [config("async-missing"), config("async-id", checkpoint_id="absent")]:
                self.assertIsNone(await saver.aget_tuple(cfg))
                self.assertEqual([item async for item in saver.alist(cfg)], [])
        asyncio.run(run())
        self.assertEqual(snapshot(saver), before)

    def test_guard_does_not_turn_existing_checkpoints_into_misses(self):
        saver = GuardedSaver()
        graph = populate(saver)
        latest = saver.get_tuple(config("existing"))
        self.assertIsNotNone(latest)
        self.assertEqual(latest.checkpoint["channel_values"]["count"], 2)
        self.assertEqual(saver.get_tuple(latest.config), latest)
        self.assertEqual(len(list(saver.list(config("existing"), limit=1))), 1)
        self.assertTrue(list(saver.list(None)))
        self.assertEqual(list(saver.list(config("existing"), filter={"step": -999})), [])
        self.assertTrue(list(saver.list(config("existing"), before=latest.config)))
        self.assertEqual(graph.invoke({"count": 9}, config("existing"))["count"], 10)
