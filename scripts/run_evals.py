#!/usr/bin/env python3
"""Run deterministic framework fixtures. This does not evaluate model capability."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evals/runs/latest-local.json")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "evals/cases"), top_level_dir=str(ROOT))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    text = stream.getvalue().replace(str(ROOT), "<repo>")
    print(text, end="")
    runtime = ROOT / "skills/agent-composition-review"
    skill_paths = [runtime / "SKILL.md", runtime / "agents/openai.yaml"] + sorted((runtime / "references").glob("*.md"))
    skill_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in skill_paths}
    record = {
        "kind": "deterministic_framework_regression_not_model_eval",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.system(),
        "packages": {name: importlib.metadata.version(name) for name in
                     ["langgraph", "langgraph-checkpoint", "langchain-core"]},
        "skill_sha256": hashlib.sha256((runtime / "SKILL.md").read_bytes()).hexdigest(),
        "skill_files_sha256": skill_hashes,
        "skill_tree_sha256": hashlib.sha256(json.dumps(skill_hashes, sort_keys=True).encode()).hexdigest(),
        "fixture_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted((ROOT / "evals/cases").glob("test_*.py"))},
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "passed": result.wasSuccessful() and result.testsRun > 0 and not result.skipped,
        "output": text,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
