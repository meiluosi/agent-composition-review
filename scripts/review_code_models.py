#!/usr/bin/env python3
"""One fresh, condition-blinded auxiliary review per completed repair attempt."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import random
import tempfile
from run_code_repair_eval import RUN, digest, verify, write, cli_args, invoke
from evals.code_repair.protocol import REVIEW_PROMPT, review_input, review_valid


def review_one(job, case, manifest):
    key=digest(job)[:20]; repair=RUN/'raw-runs'/key; out=repair/'review'
    if (out/'record.json').exists():return json.loads((out/'record.json').read_text())
    out.mkdir(exist_ok=True)
    if (out/'started.json').exists():raise RuntimeError('Unfinished review; no silent retry: '+key)
    r=json.loads((repair/'record.json').read_text())
    app=(repair/'artifacts/app.py').read_text() if (repair/'artifacts/app.py').exists() else ''
    try:model=json.loads((repair/'artifacts/model.json').read_text())
    except (OSError,ValueError):model=None
    packet=review_input(case,case['source'],app,model,r['structure'],r['acceptance'])
    text=REVIEW_PROMPT+'\n'+json.dumps(packet,ensure_ascii=False)
    write(out/'started.json',{'input_sha256':digest(text.encode())})
    with tempfile.TemporaryDirectory(prefix='composition-blind-') as directory:
        args=cli_args(Path(directory),manifest,out/'prediction.json',RUN/'review.schema.json',sandbox='read-only')
        args+=['--disable','shell_tool','--disable','unified_exec']
        meta=invoke(args,text,out,manifest['review_timeout_seconds'])
    tool_items=[e['item'] for e in meta.pop('events') if e.get('type')=='item.completed'
                and e.get('item',{}).get('type') not in ('agent_message','reasoning','error','todo_list')]
    try:prediction=json.loads((out/'prediction.json').read_text())
    except (OSError,ValueError):prediction=None
    valid=meta['completed'] and meta['exit_code']==0 and not meta['timed_out'] and not tool_items and review_valid(prediction)
    record={**meta,'valid':bool(valid),'prediction':prediction,'tool_items':tool_items,
            'input_sha256':digest(text.encode()),'event_sha256':digest((out/'events.jsonl').read_bytes())}
    write(out/'record.json',record)
    return {'key':key,'valid':record['valid'],'seconds':round(meta['elapsed_seconds'],1)}


def main():
    m,cs=verify()
    assert all((RUN/'raw-runs'/digest(j)[:20]/'record.json').exists() for j in m['schedule']), 'Finish all repairs before reviews'
    jobs=m['schedule'][:];random.Random(202609302).shuffle(jobs)
    with ThreadPoolExecutor(max_workers=m['concurrency']) as pool:
        futures=[pool.submit(review_one,j,cs[j['case_id']],m) for j in jobs]
        for n,f in enumerate(as_completed(futures),1):print(json.dumps({'finished':n,**f.result()}),flush=True)


if __name__=='__main__':main()
