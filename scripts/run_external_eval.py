#!/usr/bin/env python3
"""Run the frozen paired pilot with fresh, tool-disabled Codex CLI sessions."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.external.core import digest, make_prompt, SCHEMA, valid_prediction

EXT=ROOT/"evals/external"
DISABLED=["shell_tool","unified_exec","plugins","apps","multi_agent","browser_use",
          "computer_use","image_generation","view_image","skill_search"]


def run_one(job, case, taxonomy, bundle, manifest, timeout, cli_version):
    key=digest(job)[:20]
    out=EXT/"raw-runs"/key
    out.mkdir(parents=True,exist_ok=True)
    record_path=out/"record.json"
    if record_path.exists(): return json.loads(record_path.read_text())
    prompt=make_prompt(case["input"],taxonomy if case["dataset"]=="Who&When Pro" else None,
                       bundle if job["condition"]=="with_skill" else None)
    expected=next(x for x in manifest["cases"] if x["case_id"]==job["case_id"])
    assert digest(prompt.encode())==expected["prompt_sha256"][job["condition"]]
    env=os.environ.copy()
    # Reuse the user's current OS proxy; do not change global network settings.
    for scheme,url in urllib.request.getproxies().items():
        if scheme in ("http","https") and url:
            env.setdefault(scheme.upper()+"_PROXY",url)
    events_path=out/"events.jsonl";stderr_path=out/"stderr.txt";prediction_path=out/"prediction.json"
    started=datetime.now(timezone.utc).isoformat();t0=time.monotonic();timed_out=False
    with tempfile.TemporaryDirectory(prefix="agent-attribution-") as cwd:
        args=["codex","exec","--ignore-user-config","--ephemeral","--skip-git-repo-check",
              "--sandbox","read-only","-C",cwd,"-m",manifest["model_requested"],
              "-c",'model_reasoning_effort="'+manifest["reasoning_effort"]+'"',
              "-c",'web_search="disabled"',"--enable","skip_host_skill_discovery",
              "--disable","unbounded_connection_retries","--json",
              "--output-schema",str(EXT/"prediction.schema.json"),"-o",str(prediction_path),"-"]
        for feature in DISABLED: args += ["--disable",feature]
        with events_path.open("w") as stdout,stderr_path.open("w") as stderr:
            proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
            try: proc.communicate(prompt.encode(),timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out=True;os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
            exit_code=proc.returncode
    events=[]
    for line in events_path.read_text().splitlines():
        try: events.append(json.loads(line))
        except json.JSONDecodeError: pass
    completed=any(x.get("type")=="turn.completed" for x in events)
    usage=next((x.get("usage") for x in reversed(events) if x.get("type")=="turn.completed"),None)
    tool_items=[x for x in events if x.get("type")=="item.completed" and
                x.get("item",{}).get("type") not in {"agent_message","reasoning","error"}]
    prediction=None
    if prediction_path.exists():
        try: prediction=json.loads(prediction_path.read_text())
        except json.JSONDecodeError: pass
    status="completed" if exit_code==0 and completed and valid_prediction(prediction) and not tool_items else (
        "isolation_violation" if tool_items else "invalid_output" if completed else "infrastructure_failure")
    result={**job,"run_key":key,"status":status,"started_at":started,"elapsed_seconds":time.monotonic()-t0,
            "exit_code":exit_code,"timed_out":timed_out,"usage":usage,"prediction":prediction,
            "model_requested":manifest["model_requested"],"model_snapshot":None,
            "reasoning_effort":manifest["reasoning_effort"],"cli_version":cli_version,
            "prompt_sha256":digest(prompt.encode()),"event_sha256":digest(events_path.read_bytes()),
            "stderr_sha256":digest(stderr_path.read_bytes()),"tool_item_count":len(tool_items),
            "billing":"ChatGPT-backed Codex login; monetary cost not exposed","cost":None}
    record_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--timeout",type=int,default=240)
    args=parser.parse_args()
    manifest=json.loads((EXT/"pilot-manifest.json").read_text())
    assert digest((EXT/"core.py").read_bytes())==manifest["core_sha256"],"Core changed since freeze"
    assert digest(json.loads((EXT/"prediction.schema.json").read_text()))==manifest["schema_sha256"]
    assert digest((EXT/"sources.lock.json").read_bytes())==manifest["source_lock_sha256"]
    inputs=json.loads((EXT/"private/inputs.json").read_text())
    cases={x["case_id"]:x for x in inputs}
    for c in manifest["cases"]: assert digest(cases[c["case_id"]]["input"])==c["input_sha256"]
    taxonomy=json.loads((EXT/"private/taxonomy.json").read_text())
    bundle=(EXT/"private/skill-bundle.txt").read_text()
    version=subprocess.check_output(["codex","--version"],text=True).strip()
    jobs=[j for j in manifest["schedule"] if not (EXT/"raw-runs"/digest(j)[:20]/"record.json").exists()]
    if args.limit: jobs=jobs[:args.limit]
    runtime={"started_at":datetime.now(timezone.utc).isoformat(),"cli_version":version,
             "manifest_sha256":digest((EXT/"pilot-manifest.json").read_bytes()),
             "runner_sha256":digest(Path(__file__).read_bytes()),"timeout_seconds":args.timeout,
             "max_workers":manifest["concurrency"],"planned_runs":len(manifest["schedule"]),
             "disabled_features":DISABLED,"skip_host_skill_discovery":True,
             "user_config_ignored":True,"per_case_fresh_process":True,"remaining_in_this_invocation":len(jobs)}
    (EXT/"runtime.json").write_text(json.dumps(runtime,indent=2)+"\n")
    print(json.dumps({"pending_runs":len(jobs),"cli":version}),flush=True)
    with ThreadPoolExecutor(max_workers=manifest["concurrency"]) as pool:
        futures=[pool.submit(run_one,j,cases[j["case_id"]],taxonomy,bundle,manifest,args.timeout,version) for j in jobs]
        for n,future in enumerate(as_completed(futures),1):
            r=future.result()
            print(json.dumps({"finished":n,"total":len(jobs),"case_id":r["case_id"],"condition":r["condition"],
                 "trial":r["trial"],"status":r["status"],"seconds":round(r["elapsed_seconds"],1),"usage":r["usage"]}),flush=True)
    return 0


if __name__=="__main__": raise SystemExit(main())
