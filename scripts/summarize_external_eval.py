#!/usr/bin/env python3
"""Verify the frozen run, score every planned prediction, and export auditable tables."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.external.core import digest, score

EXT=ROOT/"evals/external"
RESULTS=EXT/"results/pilot-2026-09-29"
CONDITIONS=["without_skill","with_skill"]
METRICS=["location","agent","joint","error_mode"]


def cluster_interval(matrix, seed=20260929, replicates=10000):
    """Rows are unique tasks; columns are conditions, after averaging trials."""
    matrix=np.asarray(matrix,dtype=float)
    if not len(matrix): return None
    rng=np.random.default_rng(seed)
    indices=rng.integers(0,len(matrix),size=(replicates,len(matrix)))
    draws=matrix[indices].mean(axis=1)
    means=matrix.mean(axis=0)
    intervals=np.quantile(draws,[.025,.975],axis=0)
    difference=draws[:,1]-draws[:,0]
    return {"without_skill":{"mean":float(means[0]),"ci95":intervals[:,0].tolist()},
            "with_skill":{"mean":float(means[1]),"ci95":intervals[:,1].tolist()},
            "paired_difference":{"mean":float(means[1]-means[0]),
                                 "ci95":np.quantile(difference,[.025,.975]).tolist()},
            "independent_tasks":len(matrix)}


def aggregate(manifest,rows):
    summary={}
    for dataset in sorted({c["dataset"] for c in manifest["cases"]}):
        case_ids=[c["case_id"] for c in manifest["cases"] if c["dataset"]==dataset]
        metrics={}
        for metric in METRICS:
            matrix=[]
            for cid in case_ids:
                values=[]
                for condition in CONDITIONS:
                    vals=[r["scores"][metric] for r in rows if r["case_id"]==cid and r["condition"]==condition]
                    assert len(vals)==manifest["trials"]
                    values.append(None if all(v is None for v in vals) else sum(bool(v) for v in vals)/len(vals))
                if all(v is None for v in values): continue
                assert all(v is not None for v in values)
                matrix.append(values)
            if matrix: metrics[metric]=cluster_interval(matrix)
        resources={}
        for condition in CONDITIONS:
            group=[r for r in rows if r["dataset"]==dataset and r["condition"]==condition]
            resources[condition]={"runs":len(group),"status_counts":dict(Counter(r["status"] for r in group)),
                "median_elapsed_seconds":float(np.median([r["elapsed_seconds"] for r in group])),
                "usage_totals":{k:sum((r["usage"] or {}).get(k,0) for r in group)
                                for k in ["input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens"]},
                "usage_missing_runs":sum(r["usage"] is None for r in group),"cost":None}
        summary[dataset]={"unique_tasks":len(case_ids),"metrics":metrics,"resources":resources}
    return summary


def main():
    manifest=json.loads((EXT/"pilot-manifest.json").read_text())
    for name,path in [("core_sha256",EXT/"core.py"),("runner_sha256",ROOT/"scripts/run_external_eval.py"),
                      ("analysis_plan_sha256",EXT/"ANALYSIS_PLAN.md"),("source_lock_sha256",EXT/"sources.lock.json")]:
        assert digest(path.read_bytes())==manifest[name],f"Frozen file changed: {path.name}"
    oracles={x["case_id"]:x["gold"] for x in json.loads((EXT/"private/oracles.json").read_text())}
    cases={x["case_id"]:x for x in manifest["cases"]}
    for cid,gold in oracles.items(): assert digest(gold)==cases[cid]["oracle_sha256"]
    rows=[]
    for job in manifest["schedule"]:
        path=EXT/"raw-runs"/digest(job)[:20]/"record.json"
        if not path.exists(): raise SystemExit(f"Run incomplete: missing {job}; no chart generated.")
        record=json.loads(path.read_text())
        assert datetime.fromisoformat(record["started_at"])>=datetime.fromisoformat(manifest["frozen_at"])
        for filename,field in [("events.jsonl","event_sha256"),("stderr.txt","stderr_sha256")]:
            assert digest((path.parent/filename).read_bytes())==record[field],f"Raw evidence changed: {path.parent.name}/{filename}"
        assert all(record[k]==v for k,v in job.items())
        assert record["prompt_sha256"]==cases[job["case_id"]]["prompt_sha256"][job["condition"]]
        assert record["model_requested"]==manifest["model_requested"]
        assert record["reasoning_effort"]==manifest["reasoning_effort"]
        assert record["cli_version"]==manifest["cli_version"]
        prediction=record["prediction"] if record["status"]=="completed" else None
        public_prediction=None if prediction is None else {k:v for k,v in prediction.items() if k!="reason"}
        rows.append({**job,"dataset":cases[job["case_id"]]["dataset"],"stratum":cases[job["case_id"]]["stratum"],
            "status":record["status"],"prediction":public_prediction,
            "reason_sha256":None if prediction is None else digest(prediction["reason"].encode()),
            "scores":score(prediction,oracles[job["case_id"]]),"elapsed_seconds":record["elapsed_seconds"],
            "usage":record["usage"],"started_at":record["started_at"],"model_requested":record["model_requested"],
            "model_snapshot":None,"reasoning_effort":record["reasoning_effort"],"cli_version":record["cli_version"],
            "prompt_sha256":record["prompt_sha256"],"event_sha256":record["event_sha256"],
            "private_record_sha256":digest(path.read_bytes()),"tool_item_count":record["tool_item_count"]})
    assert len({(r["case_id"],r["condition"],r["trial"]) for r in rows})==len(manifest["schedule"])
    assert len(rows)==2*manifest["trials"]*len(manifest["cases"])
    RESULTS.mkdir(parents=True,exist_ok=True)
    (RESULTS/"predictions.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n")
    summary={"kind":"external_stratified_pilot","generated_at":datetime.now(timezone.utc).isoformat(),
             "planned_runs":len(manifest["schedule"]),"recorded_runs":len(rows),
             "status_counts":dict(Counter(r["status"] for r in rows)),
             "unique_tasks":len(cases),"trials_per_task_condition":manifest["trials"],
             "primary_metric":"exact_location_accuracy","interval":"paired task-cluster percentile bootstrap, 10000 replicates, seed 20260929",
             "model_requested":manifest["model_requested"],"model_snapshot":None,"reasoning_effort":manifest["reasoning_effort"],
             "manifest_sha256":digest((EXT/"pilot-manifest.json").read_bytes()),
             "predictions_sha256":digest((RESULTS/"predictions.json").read_bytes()),
             "summarizer_sha256":digest(Path(__file__).read_bytes()),
             "datasets":aggregate(manifest,rows),"limitations":manifest["limitations"]}
    (RESULTS/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status_counts":summary["status_counts"],"location":{k:v["metrics"]["location"] for k,v in summary["datasets"].items()}},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
