#!/usr/bin/env python3
"""Separately registered reviews after quota failure; never overwrite original attempts."""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime,timezone
import json
from pathlib import Path
import random
import subprocess
import tempfile
from run_code_repair_eval import ROOT,RUN,digest,verify,write,cli_args,invoke
from evals.code_repair.protocol import REVIEW_PROMPT,review_input,review_valid

RECOVERY=RUN/'review-recovery-2026-10-04'


def packet(job,cases):
    folder=RUN/'results'/digest(job)[:20];r=json.loads((folder/'record.json').read_text())
    app=(folder/'artifacts/app.py').read_text();model=json.loads((folder/'artifacts/model.json').read_text())
    c=cases[job['case_id']]
    value=review_input(c,c['source'],app,model,r['structure'],r['acceptance'])
    return REVIEW_PROMPT+'\n'+json.dumps(value,ensure_ascii=False)


def freeze():
    original,cases=verify(check_cli=False)
    assert not (RECOVERY/'manifest.json').exists(), 'Recovery already frozen'
    selected=[];excluded=[]
    for job in original['schedule']:
        folder=RUN/'results'/digest(job)[:20]
        try:json.loads((folder/'artifacts/model.json').read_text())
        except (OSError,ValueError):excluded.append({**job,'reason':'No parseable model artifact'});continue
        selected.append(job)
    random.Random(202610041).shuffle(selected)
    m={'registered_at':datetime.now(timezone.utc).isoformat(),'model_requested':original['model_requested'],
       'reasoning_effort':original['reasoning_effort'],'cli_version':subprocess.check_output(['codex','--version'],text=True).strip(),
       'timeout_seconds':180,'concurrency':3,'schedule':selected,'excluded':excluded,
       'selection':'All parseable model artifacts, including interrupted repairs. No repair reruns.',
       'original_manifest_sha256':digest((RUN/'manifest.json').read_bytes()),
       'protocol_sha256':digest((RECOVERY/'PROTOCOL.md').read_bytes()),'runner_sha256':digest(Path(__file__).read_bytes()),
       'input_sha256':{digest(j)[:20]:digest(packet(j,cases).encode()) for j in selected}}
    write(RECOVERY/'manifest.json',m);print('Registered '+str(len(selected))+' recovered reviews; '+str(len(excluded))+' missing models excluded.')


def load():
    _,cases=verify(check_cli=False);m=json.loads((RECOVERY/'manifest.json').read_text())
    assert digest(Path(__file__).read_bytes())==m['runner_sha256']
    assert digest((RECOVERY/'PROTOCOL.md').read_bytes())==m['protocol_sha256']
    assert digest((RUN/'manifest.json').read_bytes())==m['original_manifest_sha256']
    for job in m['schedule']:assert digest(packet(job,cases).encode())==m['input_sha256'][digest(job)[:20]]
    return m,cases


def one(job,m,cases):
    key=digest(job)[:20];out=RECOVERY/'raw-runs'/key;out.mkdir(parents=True,exist_ok=True)
    if (out/'record.json').exists():return json.loads((out/'record.json').read_text())
    assert not (out/'started.json').exists(), 'Interrupted recovery attempt; investigate, do not silently retry'
    text=packet(job,cases);write(out/'started.json',{'time':datetime.now(timezone.utc).isoformat(),'input_sha256':digest(text.encode())})
    with tempfile.TemporaryDirectory(prefix='composition-recovered-review-') as d:
        args=cli_args(Path(d),m,out/'prediction.json',RUN/'review.schema.json',sandbox='read-only')
        args+=['--disable','shell_tool','--disable','unified_exec']
        meta=invoke(args,text,out,m['timeout_seconds'])
    events=meta.pop('events')
    tool_items=[e['item'] for e in events if e.get('type')=='item.completed'
                and e.get('item',{}).get('type') not in ('agent_message','reasoning','error','todo_list')]
    errors=[e.get('message',e.get('error',{}).get('message','')) for e in events if e.get('type') in ('error','turn.failed')]
    try:prediction=json.loads((out/'prediction.json').read_text())
    except (OSError,ValueError):prediction=None
    valid=meta['completed'] and meta['exit_code']==0 and not meta['timed_out'] and not tool_items and review_valid(prediction)
    result={**job,**meta,'valid':bool(valid),'prediction':prediction,'tool_items':tool_items,'errors':errors,
            'input_sha256':digest(text.encode()),'event_sha256':digest((out/'events.jsonl').read_bytes()),
            'cli_version':m['cli_version'],'model_requested':m['model_requested'],'reasoning_effort':m['reasoning_effort']}
    write(out/'record.json',result)
    # Only structured review outputs are published; raw service logs remain ignored.
    write(RECOVERY/'results'/f'{key}.json',result)
    print(json.dumps({'key':key,'valid':bool(valid),'seconds':round(meta['elapsed_seconds'],1),'errors':errors},ensure_ascii=False),flush=True)
    return result


def run(first_only=False):
    m,cases=load();assert subprocess.check_output(['codex','--version'],text=True).strip()==m['cli_version']
    pending=[j for j in m['schedule'] if not (RECOVERY/'raw-runs'/digest(j)[:20]/'record.json').exists()]
    existing=[json.loads(p.read_text()) for p in (RECOVERY/'results').glob('*.json')]
    assert all(r['valid'] for r in existing), 'Recovery failure recorded: stop and investigate instead of continuing automatically'
    if first_only:
        if pending:one(pending[0],m,cases)
        return
    assert existing,'Run and inspect the first registered attempt before launching the remainder'
    jobs=iter(pending)
    with ThreadPoolExecutor(max_workers=m['concurrency']) as pool:
        active={pool.submit(one,j,m,cases) for j in [next(jobs,None) for _ in range(m['concurrency'])] if j is not None}
        stopped=False
        while active:
            done,active=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                result=future.result()
                if not result['valid']:stopped=True
            if not stopped:
                for _ in done:
                    job=next(jobs,None)
                    if job is not None:active.add(pool.submit(one,job,m,cases))
        if stopped:raise SystemExit('Stopped scheduling after a failed recovered review; all existing attempts retained.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['freeze','first','run','verify']);a=parser.parse_args().action
    if a=='freeze':freeze()
    elif a=='verify':load();print('Recovery inputs and protocol verified.')
    else:run(a=='first')
