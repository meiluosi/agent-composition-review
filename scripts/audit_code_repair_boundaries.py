#!/usr/bin/env python3
"""Separate post-hoc boundary probes; never modifies frozen scores or task labels."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from run_code_repair_eval import ROOT,RUN,ARMS,digest,verify,write
from evals.code_repair_followup.check_cleanup import check
from evals.code_repair_followup.check_transaction import check as check_transaction


def probe(path,family):
    try:
        script='check_cleanup.py' if family=='async_group' else 'check_transaction.py'
        proc=subprocess.run([sys.executable,'-B',str(ROOT/'evals/code_repair_followup'/script),str(path)],
                            text=True,capture_output=True,timeout=10)
        result=json.loads(proc.stdout)
        if proc.returncode:result['passed']=False
        return result
    except (subprocess.TimeoutExpired,ValueError,OSError) as error:
        return {'passed':False,'probe_error':type(error).__name__}


def main():
    manifest,cases=verify(check_cli=False);rows=[]
    for job in manifest['schedule']:
        family=cases[job['case_id']]['family']
        if family not in ('async_group','transaction'):continue
        folder=RUN/'results'/digest(job)[:20]
        record=json.loads((folder/'record.json').read_text())
        path=folder/'artifacts/app.py'
        assert digest(path.read_bytes())==record['artifacts']['app.py']
        result=probe(path,family)
        rows.append({**job,'family':family,'source_sha256':digest(path.read_bytes()),'valid_execution':record['status']=='completed',**result})
    summary={family:{arm:{'n':sum(r['condition']==arm and r['family']==family for r in rows),
                  'pass':sum(r['condition']==arm and r['family']==family and r['valid_execution'] and r['passed'] for r in rows)} for arm in ARMS}
                  for family in ('async_group','transaction')}
    normal={}
    for arm in ARMS:
        rs=[]
        for job in manifest['schedule']:
            if job['condition']==arm and job['case_id']=='c02':
                rs.append(json.loads((RUN/'results'/digest(job)[:20]/'record.json').read_text()))
        normal[arm]={'n':len(rs),'behavior_pass':sum(r['status']=='completed' and r['acceptance']['passed'] for r in rs),
                     'unchanged':sum(not r['source_changed'] for r in rs)}
    result={'kind':'post_freeze_development_probe','discovery':'After first c05 and c04 repairs; before original 36 repairs finished',
            'scope':'Cancellation during cleanup; busy commit followed by connection reuse. Separate exploratory probes.',
            'normal_label_correction':'c04 and c06 are not valid normal controls; original labels and scores remain untouched.',
            'script_sha256':{n:digest((ROOT/'evals/code_repair_followup'/n).read_bytes()) for n in ('check_cleanup.py','check_transaction.py')},
            'frozen_references':{'async_group':asyncio.run(check(ROOT/'evals/code_repair/tasks/async_group/healthy.py')),
                                 'transaction':check_transaction(ROOT/'evals/code_repair/tasks/transaction/healthy.py')},
            'revised_references':{'async_group':asyncio.run(check(ROOT/'evals/code_repair_followup/async_healthy.py')),
                                  'transaction':check_transaction(ROOT/'evals/code_repair_followup/transaction_healthy.py')},
            'conditions':summary,'valid_normal_control_c02':normal,'rows':rows}
    write(RUN/'boundary-audit.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
