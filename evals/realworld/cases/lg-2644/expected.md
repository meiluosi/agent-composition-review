# lg-2644 — nodes writing a key outside the graph's state schema

Repro: a two-node graph whose state declares only `foo`; `NodeOne` writes only
`zoo`, typed as a separate `TmpState`.

- Reported: `InvalidUpdateError: Must write to at least one of ['foo']` after
  node_1 completes.
- On the repository's pinned langgraph 1.0.3 this fixture completes with
  `{'foo': 3}` — the same non-reproduction the maintainer saw in the issue
  thread. The reporter's environment/version was inconsistent, so the reported
  error is not confirmed.
- Structural lesson (independent of the exact runtime outcome): a node can only
  update keys declared on the graph's state schema. Returning a dict typed as
  another schema does not route `zoo` into the graph.

Run: `.venv/bin/python evals/realworld/cases/lg-2644/graph.py`

The fixture is our own minimal construction; it contains no code from the issue.
Source: https://github.com/langchain-ai/langgraph/issues/2644
