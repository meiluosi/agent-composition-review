"""Consistency checks for the agent-facing skill pack."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "agent-composition-review"
REFS = SKILL / "references"


def test_skill_pack_has_required_docs() -> None:
    required = [
        SKILL / "SKILL.md",
        REFS / "case-index.md",
        REFS / "no-cli-checklist.md",
        REFS / "worked-examples.md",
        REFS / "rules.md",
        REFS / "fix-patterns.md",
        REFS / "skill-evals" / "README.md",
        REFS / "skill-evals" / "prompts.json",
        REFS / "skill-evals" / "rubric.md",
    ]
    for path in required:
        assert path.is_file(), path


def test_skill_status_and_maintenance_are_current() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "维护模式" in text
    assert "grounded" in text
    assert "experimental" in text
    assert "不宣称 benchmark" in text
    assert "v0.1 的边界" not in text
    for link in (
        "case-index.md",
        "no-cli-checklist.md",
        "worked-examples.md",
        "skill-evals/README.md",
    ):
        assert link in text


def test_case_index_covers_all_atlas_cases() -> None:
    text = (REFS / "case-index.md").read_text(encoding="utf-8")
    slugs = [
        "lg-6446",
        "lg-1964",
        "lg-8320",
        "lg-2644",
        "lg-2581",
        "lg-3875",
        "lg-9276",
        "lg-8753",
        "lg-9253",
        "lg-2336",
        "lg-vanara-bound-method",
    ]
    for slug in slugs:
        assert slug in text, slug


def test_skill_eval_prompts_are_complete() -> None:
    data = json.loads((REFS / "skill-evals" / "prompts.json").read_text(encoding="utf-8"))
    prompts = data["prompts"]
    assert len(prompts) == 10
    assert len({prompt["id"] for prompt in prompts}) == 10
    assert len({prompt["category"] for prompt in prompts}) >= 8
    for prompt in prompts:
        assert prompt["prompt"]
        assert prompt["expect"]
        assert prompt["red_flags"]
        assert prompt["reference"]


def test_worked_examples_cover_target_rules() -> None:
    text = (REFS / "worked-examples.md").read_text(encoding="utf-8")
    for rule in ("ACR001", "ACR007", "ACR106"):
        assert rule in text
    for section in ("错误假设", "证据", "最小修复", "回归断言"):
        assert section in text
