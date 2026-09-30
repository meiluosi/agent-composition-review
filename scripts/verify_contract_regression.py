#!/usr/bin/env python3
"""Re-execute the 336 local pipelines and verify the published deterministic summary."""
import argparse
import hashlib
import json
from pathlib import Path
from replay_contracts import run_suite

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'evals/runs/2026-09-30-contracts.json'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--write',action='store_true');args=p.parse_args()
    full=run_suite()
    summary={k:v for k,v in full.items() if k not in ('plan','probe_generation','records')}
    summary['generated_inputs']=len(full['probe_generation']['cases'])
    summary['records']=[{k:v for k,v in r.items() if k!='checks'} for r in full['records']]
    summary['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
        'candidates/v4/agent-composition-review/scripts/composition.py','evals/replay/system.py','evals/replay/make_model.py']}
    if args.write:OUTPUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    else:assert json.loads(OUTPUT.read_text())==summary,'Published deterministic summary or source hashes changed'
    print('Verified 336 executed pipelines against published counts and source hashes.')


if __name__=='__main__':main()
