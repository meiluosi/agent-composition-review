#!/usr/bin/env python3
"""Download pinned public data, isolate labels and freeze a stratified pilot."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import subprocess
import urllib.parse
import urllib.request
import time
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.external.core import digest, normalize_who_when, normalize_pro, make_prompt, SCHEMA

WW_REV = "59b9fcba1aaed7bbf206b5f4d3c68b8face2f49c"
PRO_REV = "0bd196c8a040841c4ae167ab33cc8151de246f1f"
PRO_SHA = "51403bfd1b6570d0a9f8e62d25a50aa62504cb1264c33a3627ead893b7e2dd51"
DATA = ROOT / "evals/external/data"
PRIVATE = ROOT / "evals/external/private"
PUBLIC = ROOT / "evals/external"


def fetch(url, path):
    if path.exists(): return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response, path.with_suffix(path.suffix + ".part").open("wb") as out:
                while chunk := response.read(1024 * 1024): out.write(chunk)
            path.with_suffix(path.suffix + ".part").replace(path)
            return path
        except Exception:
            if attempt == 2: raise
            time.sleep(0.5 * (attempt + 1))


def select_stratified(cases, n, seed):
    buckets = defaultdict(list)
    for c in cases: buckets[c["stratum"]].append(c)
    rng = random.Random(seed)
    for bucket in buckets.values(): rng.shuffle(bucket)
    strata = sorted(buckets);rng.shuffle(strata)
    selected, groups = [], set()
    while len(selected) < n:
        progressed = False
        for key in strata:
            while buckets[key]:
                case = buckets[key].pop()
                if case["group"] in groups: continue
                groups.add(case["group"]); selected.append(case); progressed = True
                break
            if len(selected) == n: break
        if not progressed: raise ValueError("Not enough unique task groups")
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--per-dataset", type=int, default=12)
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--seed", type=int, default=20260929)
    p.add_argument("--max-input-chars", type=int, default=120000)
    p.add_argument("--restore-inputs", action="store_true", help="Restore ignored input/label files against an existing frozen manifest; do not resample.")
    args = p.parse_args()
    assert args.per_dataset > 0 and args.trials > 0
    frozen_path=PUBLIC / "pilot-manifest.json"
    frozen=json.loads(frozen_path.read_text()) if frozen_path.exists() else None
    if frozen is not None and not args.restore_inputs:
        raise SystemExit("Manifest already frozen; use a new run directory for a new experiment.")
    if args.restore_inputs and frozen is None: raise SystemExit("No frozen manifest to restore")
    DATA.mkdir(parents=True, exist_ok=True); PRIVATE.mkdir(parents=True, exist_ok=True)
    # These counts and revisions were checked against the immutable source inventory.
    paths = [(kind, i) for kind, count in [("Algorithm-Generated",126),("Hand-Crafted",58)] for i in range(1,count+1)]
    def load_ww(item):
        kind, i = item
        path = DATA / "who_when" / kind / f"{i}.json"
        url = f"https://huggingface.co/datasets/Kevin355/Who_and_When/resolve/{WW_REV}/" + urllib.parse.quote(f"Who&When/{kind}/{i}.json", safe="/")
        fetch(url,path)
        return normalize_who_when(json.loads(path.read_text()),f"ww:{kind}:{i}",kind)
    with ThreadPoolExecutor(max_workers=8) as pool: ww = list(pool.map(load_ww, paths))
    base = f"https://huggingface.co/datasets/Leoxx/whowhen_pro/resolve/{PRO_REV}/"
    pro_path = fetch(base + "data/text.jsonl", DATA / "pro_text.jsonl")
    assert digest(pro_path.read_bytes()) == PRO_SHA, "Pinned Pro file content changed"
    taxonomy_path = fetch(base + "taxonomy.yaml", DATA / "pro_taxonomy.yaml")
    taxonomy = yaml.safe_load(taxonomy_path.read_text())
    rejected = Counter(); pro=[]; first_by_framework=set(); dev_ids=set()
    with pro_path.open() as handle:
        for line in handle:
            row=json.loads(line)
            # Exclude schema-development examples and earlier data-viewer previews.
            if row["framework"] not in first_by_framework:
                first_by_framework.add(row["framework"]); dev_ids.add("pro:" + row["id"])
            if row["id"].startswith("alfworld_alfagent_") and int(row["id"].rsplit("_",1)[1]) <= 15:
                dev_ids.add("pro:" + row["id"])
            try: pro.append(normalize_pro(row))
            except (ValueError, KeyError, TypeError) as error: rejected[str(error)] += 1
    dev_ids.add("ww:Algorithm-Generated:1")
    dev_groups={c["group"] for c in ww + pro if c["case_id"] in dev_ids}
    pools={}; exclusions={}
    for name,cases in [("Who&When",ww),("Who&When Pro",pro)]:
        excluded=Counter(); pool=[]
        for c in cases:
            if c["group"] in dev_groups: excluded["development_group"]+=1;continue
            if len(json.dumps(c["input"],ensure_ascii=False))>args.max_input_chars:
                excluded["over_context_character_cap"]+=1;continue
            pool.append(c)
        pools[name]=pool; exclusions[name]=dict(excluded)
    selected=[]
    rng=random.Random(args.seed)
    if frozen is not None:
        by_id={c["case_id"]:c for c in ww+pro}
        selected=[by_id[c["case_id"]] for c in frozen["cases"]]
        skill_root=PUBLIC/"skill-snapshot"
    else:
        for idx,name in enumerate(pools): selected.extend(select_stratified(pools[name],args.per_dataset,args.seed+idx))
        rng.shuffle(selected);skill_root=ROOT/"skills/agent-composition-review"
    skill_paths=[skill_root/"SKILL.md",*sorted((skill_root/"references").glob("*.md"))]
    skill_hashes={str(x.relative_to(skill_root)):digest(x.read_bytes()) for x in skill_paths}
    bundle="\n\n".join("FILE: "+str(x.relative_to(skill_root))+"\n"+x.read_text() for x in skill_paths)
    records=[];inputs=[];oracles=[]
    for case in selected:
        tax=taxonomy if case["dataset"]=="Who&When Pro" else None
        inputs.append({k:v for k,v in case.items() if k!="gold"})
        oracles.append({"case_id":case["case_id"],"gold":case["gold"]})
        records.append({k:case[k] for k in ["case_id","dataset","stratum","group"]} | {
            "input_chars":len(json.dumps(case["input"],ensure_ascii=False)),
            "input_sha256":digest(case["input"]),"oracle_sha256":digest(case["gold"]),
            "prompt_sha256":{condition:digest(make_prompt(case["input"],tax,bundle if condition=="with_skill" else None).encode())
                             for condition in ["with_skill","without_skill"]}})
    def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
    files={str(x.relative_to(DATA)):digest(x.read_bytes()) for x in sorted((DATA/"who_when").rglob("*.json"))}
    files.update({"pro_text.jsonl":PRO_SHA,"pro_taxonomy.yaml":digest(taxonomy_path.read_bytes())})
    if frozen is not None:
        lock=json.loads((PUBLIC/"sources.lock.json").read_text())
        assert files==lock["files_sha256"],"Downloaded file hashes differ from frozen source inventory"
        assert records==frozen["cases"],"Restored cases differ from frozen inputs, labels or prompts"
        assert skill_hashes==frozen["skill_files_sha256"],"Skill snapshot differs from frozen treatment"
    write(PRIVATE/"inputs.json",inputs);write(PRIVATE/"oracles.json",oracles)
    (PRIVATE/"skill-bundle.txt").write_text(bundle);write(PRIVATE/"taxonomy.json",taxonomy)
    if frozen is not None:
        print(f"Restored and hash-verified {len(records)} frozen cases; manifest unchanged.")
        return
    write(PUBLIC/"prediction.schema.json",SCHEMA)
    write(PUBLIC/"sources.lock.json",{"who_when_revision":WW_REV,"pro_revision":PRO_REV,"files_sha256":files,
          "raw_counts":{"Who&When":len(ww),"Who&When Pro text":len(pro)+sum(rejected.values())},
          "mapping_exclusions":dict(rejected),"pool_exclusions":exclusions,
          "eligible_counts":{k:len(v) for k,v in pools.items()},
          "AgentRx":{"status":"blocked_access","revision":"88e871fecb58b2d090449f37ec80b8865594e0b5",
                     "reason":"Dataset gated; no HF_TOKEN configured, access terms not accepted in this task."}})
    order=[]
    for trial in range(1,args.trials+1):
        sequence=list(records);rng.shuffle(sequence)
        for case in sequence:
            conditions=["with_skill","without_skill"];rng.shuffle(conditions)
            order.extend({"case_id":case["case_id"],"condition":c,"trial":trial} for c in conditions)
    manifest={"schema_version":1,"frozen_at":datetime.now(timezone.utc).isoformat(),
       "kind":"stratified_external_pilot_not_full_benchmark","seed":args.seed,
       "per_dataset":args.per_dataset,"trials":args.trials,"max_input_chars":args.max_input_chars,
       "conditions":["with_skill","without_skill"],"cases":records,"schedule":order,
       "model_requested":"gpt-6-astra","reasoning_effort":"high","concurrency":3,
       "skill_files_sha256":skill_hashes,"skill_tree_sha256":digest(skill_hashes),
       "source_lock_sha256":digest((PUBLIC/"sources.lock.json").read_bytes()),
       "core_sha256":digest((ROOT/"evals/external/core.py").read_bytes()),
       "runner_sha256":digest((ROOT/"scripts/run_external_eval.py").read_bytes()),
       "analysis_plan_sha256":digest((PUBLIC/"ANALYSIS_PLAN.md").read_bytes()),
       "cli_version":subprocess.check_output(["codex","--version"],text=True).strip(),
       "timeout_seconds":240,
       "schema_sha256":digest(SCHEMA),"primary_metric":"exact_location_accuracy",
       "limitations":["Public data may have been seen in pretraining; not a contamination-free holdout.",
                      "Frozen skill is not tuned on selected cases; within-case trials are clustered, not independent tasks.",
                      "Full skill and references are injected inline; skill discovery and interactive questioning are not evaluated.",
                      "Who&When uses strict equality, unlike the inspected upstream substring scorer.",
                      "Pro macnet location mapping is excluded pending verification; unsupported and overlength cases are reported.",
                      "Only failed traces; this run cannot estimate healthy-system false-positive rate.",
                      "Model alias is requested explicitly; backend snapshot identifier is not exposed by this runner."]}
    write(PUBLIC/"pilot-manifest.json",manifest)
    print(json.dumps({"cases":len(records),"model_runs":len(order),"strata":dict(Counter((x['dataset']+' / '+x['stratum']) for x in records)),"eligible":{k:len(v) for k,v in pools.items()},"mapping_exclusions":dict(rejected)},ensure_ascii=False,indent=2))


if __name__=="__main__": main()
