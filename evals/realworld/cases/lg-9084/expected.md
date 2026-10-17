# lg-9084 — tags duplicated by nested subgraphs

Repro: build the same state graph at nesting depths 0..3 and read
`config["tags"]` in the leaf node.

- Expected: the invoke-time tag appears once at every depth.
- Observed on langgraph 1.2.12: 1x, 2x, 4x, 8x.
- On the repository's pinned langgraph 1.0.3 this fixture prints 1x at every
  depth, because the duplication starts in 1.2.3 (ensure_config merges explicit
  tags onto the ambient tags at every graph boundary).

Run: `.venv/bin/python evals/realworld/cases/lg-9084/graph.py`

The fixture is our own minimal construction; it contains no code from the issue.
Source: https://github.com/langchain-ai/langgraph/issues/9084
