#!/usr/bin/env python3
"""Freeze or execute the four-condition, 96-run development ablation."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.external.core import digest, make_prompt, valid_prediction, SCHEMA

EXT = ROOT / "evals/external"
RUN = ROOT / "evals/iterations/ablation-2026-09-29"
CONDITIONS = ["A_no_skill", "B_v1_full", "C_v1_core", "D_v2_core"]
CASE_IDS = ["ww:Algorithm-Generated:50", "ww:Algorithm-Generated:88",
            "ww:Algorithm-Generated:68", "ww:Hand-Crafted:6", "ww:Hand-Crafted:33",
            "pro:alfworld_alfagent_0288", "pro:math_debate_0084", "pro:dacode_smolagents_0096"]
DISABLED = ["shell_tool", "unified_exec", "plugins", "apps", "multi_agent", "browser_use",
            "computer_use", "image_generation", "view_image", "skill_search"]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def materials():
    cases = {r["case_id"]: r for r in json.loads((EXT / "private/inputs.json").read_text())}
    taxonomy = json.loads((EXT / "private/taxonomy.json").read_text())
    bundles = {"A_no_skill": None}
    for condition in CONDITIONS[1:]:
        bundles[condition] = (RUN / "snapshots" / (condition + ".txt")).read_text()
    return cases, taxonomy, bundles


def prompt_for(case, condition, taxonomy, bundles):
    return make_prompt(case["input"], taxonomy if case["dataset"] == "Who&When Pro" else None,
                       bundles[condition])


def freeze():
    path = RUN / "manifest.json"
    if path.exists(): raise SystemExit("Already frozen; refusing to overwrite.")
    pilot = json.loads((EXT / "pilot-manifest.json").read_text())
    assert digest((EXT / "core.py").read_bytes()) == pilot["core_sha256"]
    (RUN / "snapshots").mkdir(parents=True, exist_ok=True)
    old = EXT / "skill-snapshot"
    skill_paths = [old / "SKILL.md", *sorted((old / "references").glob("*.md"))]
    for p in skill_paths: assert digest(p.read_bytes()) == pilot["skill_files_sha256"][str(p.relative_to(old))]
    full = "\n\n".join("FILE: " + str(p.relative_to(old)) + "\n" + p.read_text() for p in skill_paths)
    core = "FILE: SKILL.md\n" + (old / "SKILL.md").read_text()
    candidate = ROOT / "evals/iterations/v2-candidate/SKILL.md"
    for condition, text in [("B_v1_full", full), ("C_v1_core", core),
                            ("D_v2_core", "FILE: SKILL.md\n" + candidate.read_text())]:
        (RUN / "snapshots" / (condition + ".txt")).write_text(text)
    cases, taxonomy, bundles = materials()
    oracles = {r["case_id"]: r["gold"] for r in json.loads((EXT / "private/oracles.json").read_text())}
    old_cases = {c["case_id"]: c for c in pilot["cases"]}
    records = []
    for cid in CASE_IDS:
        c = cases[cid]
        assert digest(c["input"]) == old_cases[cid]["input_sha256"]
        assert digest(oracles[cid]) == old_cases[cid]["oracle_sha256"]
        prompts = {arm: digest(prompt_for(c, arm, taxonomy, bundles).encode()) for arm in CONDITIONS}
        assert prompts["A_no_skill"] == old_cases[cid]["prompt_sha256"]["without_skill"]
        assert prompts["B_v1_full"] == old_cases[cid]["prompt_sha256"]["with_skill"]
        records.append({k: old_cases[cid][k] for k in ["case_id", "dataset", "stratum", "group", "input_sha256", "oracle_sha256"]}
                       | {"prompt_sha256": prompts})
    rng = random.Random(20260930)
    schedule = []
    for trial in range(1, 4):
        ids = list(CASE_IDS); rng.shuffle(ids)
        for cid in ids:
            arms = list(CONDITIONS); rng.shuffle(arms)
            schedule.extend({"case_id": cid, "condition": arm, "trial": trial} for arm in arms)
    manifest = {
        "kind": "development_ablation_not_holdout", "frozen_at": datetime.now(timezone.utc).isoformat(),
        "seed": 20260930, "trials": 3, "concurrency": 3, "timeout_seconds": 240,
        "conditions": CONDITIONS, "cases": records, "schedule": schedule,
        "model_requested": pilot["model_requested"], "reasoning_effort": pilot["reasoning_effort"],
        "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "source_pilot_sha256": digest((EXT / "pilot-manifest.json").read_bytes()),
        "core_sha256": digest((EXT / "core.py").read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "analysis_plan_sha256": digest((RUN / "ANALYSIS_PLAN.md").read_bytes()),
        "schema_sha256": digest(SCHEMA), "taxonomy_sha256": digest(taxonomy),
        "snapshot_sha256": {arm: digest(bundles[arm].encode()) for arm in CONDITIONS[1:]},
        "candidate_source_sha256": digest(candidate.read_bytes()),
        "limitations": ["Eight purposively selected development tasks, already used to design v2.",
                        "Core-only conditions cannot read referenced files; not native skill routing.",
                        "Trial numbers do not fix model randomness; backend snapshot is unavailable.",
                        "Exact label matching does not settle disputed causal interpretations.",
                        "No instruction changes or selective retries during this experiment."]}
    write_json(path, manifest)
    write_json(RUN / "prediction.schema.json", SCHEMA)
    print("Frozen 8 tasks × 4 conditions × 3 trials = 96 runs; A/B prompts match original pilot exactly.")


def verify(manifest):
    checks = {"source_pilot_sha256": EXT / "pilot-manifest.json", "core_sha256": EXT / "core.py",
              "runner_sha256": Path(__file__), "analysis_plan_sha256": RUN / "ANALYSIS_PLAN.md"}
    for key, path in checks.items(): assert digest(path.read_bytes()) == manifest[key], f"Changed frozen file: {path}"
    assert digest(json.loads((RUN / "prediction.schema.json").read_text())) == manifest["schema_sha256"]
    cases, taxonomy, bundles = materials()
    assert digest(taxonomy) == manifest["taxonomy_sha256"]
    for arm in CONDITIONS[1:]: assert digest(bundles[arm].encode()) == manifest["snapshot_sha256"][arm]
    for c in manifest["cases"]:
        assert digest(cases[c["case_id"]]["input"]) == c["input_sha256"]
        for arm in CONDITIONS:
            assert digest(prompt_for(cases[c["case_id"]], arm, taxonomy, bundles).encode()) == c["prompt_sha256"][arm]
    version = subprocess.check_output(["codex", "--version"], text=True).strip()
    assert version == manifest["cli_version"], "CLI changed; do not silently mix versions"
    return cases, taxonomy, bundles


def run_one(job, prompt, manifest):
    out = RUN / "raw-runs" / digest(job)[:20]
    out.mkdir(parents=True, exist_ok=True)
    path = out / "record.json"
    if path.exists(): return json.loads(path.read_text())
    env = os.environ.copy()
    for scheme, url in urllib.request.getproxies().items():
        if scheme in ("http", "https") and url: env.setdefault(scheme.upper() + "_PROXY", url)
    events_path = out / "events.jsonl"; stderr_path = out / "stderr.txt"; pred_path = out / "prediction.json"
    started = datetime.now(timezone.utc).isoformat(); t0 = time.monotonic(); timed_out = False
    with tempfile.TemporaryDirectory(prefix="composition-ablation-") as cwd:
        args = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "read-only", "-C", cwd, "-m", manifest["model_requested"],
                "-c", 'model_reasoning_effort="' + manifest["reasoning_effort"] + '"',
                "-c", 'web_search="disabled"', "--enable", "skip_host_skill_discovery",
                "--disable", "unbounded_connection_retries", "--json", "--output-schema",
                str(RUN / "prediction.schema.json"), "-o", str(pred_path), "-"]
        for feature in DISABLED: args += ["--disable", feature]
        with events_path.open("w") as stdout, stderr_path.open("w") as stderr:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                    env=env, start_new_session=True)
            try: proc.communicate(prompt.encode(), timeout=manifest["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True; os.killpg(proc.pid, signal.SIGKILL); proc.communicate()
    events = []
    for line in events_path.read_text().splitlines():
        try: events.append(json.loads(line))
        except json.JSONDecodeError: pass
    completed = any(e.get("type") == "turn.completed" for e in events)
    usage = next((e.get("usage") for e in reversed(events) if e.get("type") == "turn.completed"), None)
    tool_items = [e for e in events if e.get("type") == "item.completed" and
                  e.get("item", {}).get("type") not in {"agent_message", "reasoning", "error"}]
    prediction = None
    if pred_path.exists():
        try: prediction = json.loads(pred_path.read_text())
        except json.JSONDecodeError: pass
    status = "completed" if proc.returncode == 0 and completed and valid_prediction(prediction) and not tool_items else (
        "isolation_violation" if tool_items else "invalid_output" if completed else "infrastructure_failure")
    record = {**job, "status": status, "started_at": started, "elapsed_seconds": time.monotonic() - t0,
              "exit_code": proc.returncode, "timed_out": timed_out, "usage": usage, "prediction": prediction,
              "model_requested": manifest["model_requested"], "reasoning_effort": manifest["reasoning_effort"],
              "cli_version": manifest["cli_version"], "model_snapshot": None, "cost": None,
              "prompt_sha256": digest(prompt.encode()), "event_sha256": digest(events_path.read_bytes()),
              "stderr_sha256": digest(stderr_path.read_bytes()), "tool_item_count": len(tool_items)}
    write_json(path, record)
    return record


def run():
    manifest = json.loads((RUN / "manifest.json").read_text())
    cases, taxonomy, bundles = verify(manifest)
    jobs = [j for j in manifest["schedule"] if not (RUN / "raw-runs" / digest(j)[:20] / "record.json").exists()]
    print(json.dumps({"pending": len(jobs), "planned": len(manifest["schedule"])}), flush=True)
    with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
        futures = [pool.submit(run_one, j, prompt_for(cases[j["case_id"]], j["condition"], taxonomy, bundles), manifest) for j in jobs]
        for n, future in enumerate(as_completed(futures), 1):
            r = future.result()
            print(json.dumps({"finished": n, "total": len(jobs), **{k: r[k] for k in ["case_id", "condition", "trial", "status"]},
                              "seconds": round(r["elapsed_seconds"], 1)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run", "verify"])
    action = parser.parse_args().action
    if action == "freeze": freeze()
    elif action == "run": run()
    else:
        verify(json.loads((RUN / "manifest.json").read_text()))
        print("All frozen input, prompt, snapshot, runner and CLI checks passed.")
