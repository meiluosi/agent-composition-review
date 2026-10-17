"""Tests for the built-in `acr demo` (skipped when langgraph is absent)."""

from __future__ import annotations

import pytest

from agent_composition_review import demo
from agent_composition_review.cli import main

requires_langgraph = pytest.mark.skipif(
    not demo.langgraph_available(),
    reason='langgraph is not installed (pip install "agent-composition-review[langgraph]")',
)


@requires_langgraph
def test_demo_runs_broken_then_fixed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "InvalidUpdateError" in out
    assert "ACR001" in out
    assert "ACR002" in out
    assert "acr scan found 0 finding(s)" in out
    assert "final state" in out
    assert '"b": "old|updated"' in out
    assert '"s1": 4' in out
    assert '"s2": -2' in out


def test_demo_missing_langgraph_exits_2(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo, "langgraph_available", lambda: False)
    assert main(["demo"]) == 2
    err = capsys.readouterr().err
    assert "langgraph is required" in err
    assert 'pip install "agent-composition-review[langgraph]"' in err
