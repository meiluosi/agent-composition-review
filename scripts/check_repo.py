#!/usr/bin/env python3
"""Portable structural checks for the skill, case registry and local links."""

import json
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT):
    runtime = root / "skills/agent-composition-review"
    text = (runtime / "SKILL.md").read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md needs YAML frontmatter"
    meta = yaml.safe_load(match.group(1))
    assert meta["name"] == "agent-composition-review"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta["name"])
    assert isinstance(meta["description"], str) and 0 < len(meta["description"]) <= 1024
    assert "<" not in meta["description"] and ">" not in meta["description"]
    assert "[TODO:" not in text
    ui = yaml.safe_load((runtime / "agents/openai.yaml").read_text())["interface"]
    assert "$agent-composition-review" in ui["default_prompt"]
    assert 25 <= len(ui["short_description"]) <= 64
    registry = json.loads((root / "evals/cases.json").read_text())
    assert registry["schema_version"] == 1
    ids = set()
    for case in registry["cases"]:
        assert case["id"] not in ids, "Duplicate case ID"
        ids.add(case["id"])
        assert case["split"] in ["development", "holdout"]
        assert case["source_url"].startswith("https://")
        assert (root / case["fixture"]).is_file()
        assert case["family"] and case["tested_versions"] and case["evidence_level"]
    for path in root.rglob("*.md"):
        if any(part in [".git", ".venv", "dist"] for part in path.relative_to(root).parts):
            continue
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if target.startswith(("https://", "http://", "#", "mailto:")):
                continue
            assert not target.startswith("/"), f"Nonportable link in {path.relative_to(root)}"
            assert (path.parent / target.split("#")[0]).exists(), f"Broken link: {path.relative_to(root)} -> {target}"
    assert (root / "evals/PROTOCOL.md").is_file()
    assert (root / "README.md").is_file()
    print(f"PASS: skill structure, metadata, {len(ids)} case records, and relative links")


if __name__ == "__main__":
    try:
        check()
    except (AssertionError, KeyError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
