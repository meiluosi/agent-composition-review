#!/usr/bin/env python3
"""Freeze and run 72 development comparisons with real on-demand reference reads."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.external.core import digest
from evals.mechanisms.core import SCHEMA, audit_command, prompt, valid

RUN = ROOT / "evals/iterations/mechanisms-2026-09-29"
CASES = ROOT / "evals/mechanisms/cases.json"
ARMS = ["A_no_skill", "B_v1_routed", "C_v3_routed"]
DISABLED = ["plugins", "apps", "multi_agent", "browser_use", "computer_use", "image_generation",
            "view_image", "skill_search", "hooks", "unbounded_connection_retries"]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def runtime_files(root):
    return [root / "SKILL.md", *sorted(p for folder in ("references", "scripts", "agents")
                                      for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)]


def core_for(arm):
    return None if arm == "A_no_skill" else (RUN / "snapshots" / arm / "SKILL.md").read_text()


def freeze():
    if (RUN / "manifest.json").exists(): raise SystemExit("Already frozen; use a new experiment for changes.")
    for arm, source in [("B_v1_routed", ROOT / "evals/external/skill-snapshot"),
                        ("C_v3_routed", ROOT / "evals/iterations/v3-candidate")]:
        # V1 snapshot contains the unchanged main instructions and references.
        files = runtime_files(source)
        for path in files:
            dest = RUN / "snapshots" / arm / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, dest)
        if arm == "B_v1_routed":
            for path in files:
                assert path.read_bytes() == (ROOT / path.relative_to(source)).read_bytes()
    write(RUN / "prediction.schema.json", SCHEMA)
    cases = json.loads(CASES.read_text())["cases"]
    rng = random.Random(20260929)
    schedule = []
    for trial in (1, 2):
        order = cases[:]; rng.shuffle(order)
        for case in order:
            arms = ARMS[:]; rng.shuffle(arms)
            schedule.extend({"case_id": case["case_id"], "condition": arm, "trial": trial} for arm in arms)
    frozen = [CASES, ROOT / "evals/mechanisms/core.py", Path(__file__),
              ROOT / "scripts/analyze_mechanisms.py", RUN / "ANALYSIS_PLAN.md", RUN / "prediction.schema.json"]
    frozen += [p for p in (RUN / "snapshots").rglob("*") if p.is_file()]
    manifest = {"kind": "synthetic_development_mechanism_eval", "frozen_at": datetime.now(timezone.utc).isoformat(),
                "model_requested": "gpt-6-astra", "reasoning_effort": "high", "model_snapshot": None,
                "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
                "concurrency": 3, "timeout_seconds": 240, "trials": 2, "seed": 20260929,
                "conditions": ARMS, "schedule": schedule,
                "cases": [{"case_id": c["case_id"], "pair_id": c["pair_id"], "input_sha256": digest(c["input"]),
                           "oracle_sha256": digest(c["oracle"])} for c in cases],
                "prompt_sha256": {a: digest(prompt(core_for(a)).encode()) for a in ARMS},
                "files_sha256": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in frozen},
                "limitations": ["Authored with candidate: development cases, no holdout or natural frequency claim.",
                                "Two repeats; six paired families are the uncertainty units.",
                                "Forced core injection with actual optional reference reads, not automatic skill discovery.",
                                "Shared output contract already supplies evidence guidance; measures incremental skill benefit.",
                                "Read-only shell and observed-command audit are not a complete filesystem security boundary.",
                                "Exact backend snapshot and monetary cost unavailable.",
                                "Structured claim scoring is not a blind expert review of all prose.",
                                "No executed repair or real-world causal intervention in these synthetic logs."]}
    write(RUN / "manifest.json", manifest)
    print("Frozen 12 cases × 3 conditions × 2 repeats = 72 model runs.")


def verify(check_cli=True):
    m = json.loads((RUN / "manifest.json").read_text())
    for name, expected in m["files_sha256"].items():
        assert digest((ROOT / name).read_bytes()) == expected, f"Changed frozen file: {name}"
    for arm in ARMS: assert digest(prompt(core_for(arm)).encode()) == m["prompt_sha256"][arm]
    if check_cli:
        assert subprocess.check_output(["codex", "--version"], text=True).strip() == m["cli_version"]
    return m, {c["case_id"]: c for c in json.loads(CASES.read_text())["cases"]}


def run_one(job, case, m):
    out = RUN / "raw-runs" / digest(job)[:20]
    out.mkdir(parents=True, exist_ok=True)
    if (out / "record.json").exists(): return json.loads((out / "record.json").read_text())
    if (out / "started.json").exists(): raise RuntimeError("Unfinished previous attempt; no silent retry: " + str(job))
    started = datetime.now(timezone.utc).isoformat(); t0 = time.monotonic()
    write(out / "started.json", {**job, "started_at": started})
    env = os.environ.copy()
    for scheme, url in urllib.request.getproxies().items():
        if scheme in ("http", "https") and url: env.setdefault(scheme.upper() + "_PROXY", url)
    with tempfile.TemporaryDirectory(prefix="composition-mechanism-") as cwd:
        stage = Path(cwd)
        write(stage / "case.json", case["input"])  # explicitly never serialize case/oracle/pair metadata
        if job["condition"] != "A_no_skill":
            source = RUN / "snapshots" / job["condition"]
            for path in runtime_files(source):
                dest = stage / path.relative_to(source); dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        allowed = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
        args = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "read-only", "-C", cwd, "-m", m["model_requested"],
                "-c", 'model_reasoning_effort="' + m["reasoning_effort"] + '"',
                "-c", 'web_search="disabled"', "--enable", "skip_host_skill_discovery", "--json",
                "--output-schema", str(RUN / "prediction.schema.json"), "-o", str(out / "prediction.json"), "-"]
        for feature in DISABLED: args += ["--disable", feature]
        timed_out = False
        with (out / "events.jsonl").open("w") as stdout, (out / "stderr.txt").open("w") as stderr:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                    env=env, start_new_session=True)
            try: proc.communicate(prompt(core_for(job["condition"])).encode(), timeout=m["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True; os.killpg(proc.pid, signal.SIGKILL); proc.communicate()
        events = []
        for line in (out / "events.jsonl").read_text().splitlines():
            try: events.append(json.loads(line))
            except json.JSONDecodeError: pass
        tool_items = [e["item"] for e in events if e.get("type") == "item.completed"
                      and e.get("item", {}).get("type") not in {"agent_message", "reasoning", "error"}]
        audit = []; reads = set()
        for item in tool_items:
            ok, files = audit_command(item.get("command", ""), cwd, allowed) if item.get("type") == "command_execution" else (False, [])
            audit.append({"type": item.get("type"), "command": item.get("command", "").replace(cwd, "<workspace>"),
                          "allowed": ok, "exit_code": item.get("exit_code"), "files": files})
            if ok and item.get("exit_code") == 0: reads.update(files)
        isolation = all(a["allowed"] for a in audit)
        # Reading case.json is required to substantiate evaluation input consumption.
        consumed = "case.json" in reads
    pred = None
    try: pred = json.loads((out / "prediction.json").read_text())
    except (OSError, ValueError): pass
    completed = any(e.get("type") == "turn.completed" for e in events)
    status = ("isolation_unverified" if not isolation else "input_not_read" if completed and not consumed else
              "completed" if proc.returncode == 0 and completed and valid(pred, case) else
              "invalid_output" if completed else "infrastructure_failure")
    record = {**job, "status": status, "started_at": started, "elapsed_seconds": time.monotonic() - t0,
              "exit_code": proc.returncode, "timed_out": timed_out, "prediction": pred,
              "usage": next((e.get("usage") for e in reversed(events) if e.get("type") == "turn.completed"), None),
              "cli_version": m["cli_version"], "model_requested": m["model_requested"], "reasoning_effort": m["reasoning_effort"],
              "input_sha256": digest(case["input"]), "prompt_sha256": m["prompt_sha256"][job["condition"]],
              "event_sha256": digest((out / "events.jsonl").read_bytes()),
              "stderr_sha256": digest((out / "stderr.txt").read_bytes()),
              "tool_audit": audit, "read_files": sorted(reads), "tool_item_count": len(tool_items)}
    write(out / "record.json", record)
    return record


def run():
    m, cases = verify()
    jobs = [j for j in m["schedule"] if not (RUN / "raw-runs" / digest(j)[:20] / "record.json").exists()]
    print(json.dumps({"planned": len(m["schedule"]), "pending": len(jobs)}), flush=True)
    with ThreadPoolExecutor(max_workers=m["concurrency"]) as pool:
        futures = [pool.submit(run_one, j, cases[j["case_id"]], m) for j in jobs]
        for n, future in enumerate(as_completed(futures), 1):
            r = future.result()
            print(json.dumps({"finished": n, **{k: r[k] for k in ("case_id", "condition", "trial", "status")},
                              "seconds": round(r["elapsed_seconds"], 1)}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("action", choices=["freeze", "verify", "run"])
    action = p.parse_args().action
    if action == "freeze": freeze()
    elif action == "run": run()
    else: verify(); print("All frozen inputs, instructions, scorer, plan and CLI verified.")
