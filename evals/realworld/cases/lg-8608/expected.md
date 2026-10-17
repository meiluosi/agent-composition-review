# lg-8608 — error_handler re-raises the original exception

Repro: a node raises; an `add_node(..., error_handler=...)` handles it with a
`Command` update. With a second node in the same super-step, plain `invoke`
still raises the original exception even though the handler ran once.

- Expected: graph finishes with `{"x": "handled"}`.
- Reported on langgraph 1.2.11: `RuntimeError` escapes for parallel invoke,
  `stream_mode="custom"`/`"messages"`, and `subgraphs=True`.
- Version note: the repository's pinned langgraph 1.0.3 accepts the
  `error_handler` keyword through `**kwargs` but never calls the handler, so
  running this fixture there raises `RuntimeError` without exercising the
  reported path. Use a langgraph version >= 1.2 to reproduce.

Run (langgraph >= 1.2): `.venv/bin/python evals/realworld/cases/lg-8608/graph.py`

The fixture is our own minimal construction; it contains no code from the issue.
Source: https://github.com/langchain-ai/langgraph/issues/8608
