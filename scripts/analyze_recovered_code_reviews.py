#!/usr/bin/env python3
"""Verify and summarize the separately registered semantic reviews without model calls."""
from collections import Counter
import json
from run_code_repair_eval import RUN,ARMS,digest,write
from recover_code_reviews import RECOVERY,load
from evals.code_repair.protocol import review_valid


def analyze():
    m,_=load();rows=[]
    for job in m['schedule']:
        key=digest(job)[:20];r=json.loads((RECOVERY/'results'/f'{key}.json').read_text())
        assert all(r[k]==v for k,v in job.items())
        assert r['input_sha256']==m['input_sha256'][key]
        assert r['cli_version']==m['cli_version'] and r['model_requested']==m['model_requested']
        if r['valid']:assert review_valid(r['prediction']) and r['completed'] and r['exit_code']==0 and not r['tool_items'] and not r['timed_out']
        original=json.loads((RUN/'results'/key/'record.json').read_text())
        rows.append({**r,'repair_completed':original['status']=='completed'})
    out={'kind':'separate_quota_recovery_reviews','n':len(rows),'excluded_missing_model':len(m['excluded']),
         'cli_version':m['cli_version'],'model_requested':m['model_requested'],'conditions':{}}
    for arm in ARMS:
        rs=[r for r in rows if r['condition']==arm];valid=[r for r in rs if r['valid']]
        completed=[r for r in valid if r['repair_completed']]
        all_covered=lambda r:all(c['assessment']=='covered' for c in r['prediction']['criteria'])
        out['conditions'][arm]={'eligible':len(rs),'valid':len(valid),
          'completed_repair_eligible':sum(r['repair_completed'] for r in rs),'valid_completed_repair_reviews':len(completed),
          'all_covered_completed_repair':sum(map(all_covered,completed)),
          'all_covered_all_artifacts':sum(map(all_covered,valid)),
          'criteria_counts_all_artifacts':dict(Counter(c['assessment'] for r in valid for c in r['prediction']['criteria'])),
          'usage_missing':sum(r.get('usage') is None for r in rs),
          'tokens':{k:sum((r.get('usage') or {}).get(k,0) for r in rs) for k in ('input_tokens','cached_input_tokens','output_tokens')}}
    out['interrupted_repair_artifacts']=[{k:r[k] for k in ('case_id','condition','trial','valid','prediction')} for r in rows if not r['repair_completed']]
    write(RECOVERY/'summary.json',out);print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__=='__main__':analyze()
