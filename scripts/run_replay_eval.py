#!/usr/bin/env python3
"""Freeze and execute a matched-tools, constrained configuration repair comparison."""
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

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evals.external.core import digest
from evals.replay.experiment import cases
from evals.replay.protocol import SCHEMA, prompt, valid, audit
from evals.replay.runtime import SOURCE, composition
from evals.replay.system import run as execute_system

RUN=ROOT/'evals/iterations/replay-2026-09-30'
ARMS=['A_no_skill','B_v3','C_v4']
DISABLED=['plugins','apps','multi_agent','browser_use','computer_use','image_generation','view_image','skill_search','hooks','unbounded_connection_retries']


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def files(source):
    return sorted(p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')


def core(arm):return None if arm==ARMS[0] else (RUN/'snapshots'/arm/'SKILL.md').read_text()


def freeze():
    if (RUN/'manifest.json').exists():raise SystemExit('Already frozen; create another experiment for changes')
    for arm,source in [(ARMS[1],ROOT/'evals/iterations/v3-candidate'),(ARMS[2],SOURCE)]:
        for path in files(source):
            dest=RUN/'snapshots'/arm/path.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
    write(RUN/'prediction.schema.json',SCHEMA)
    write(RUN/'cases.json',cases())
    schedule=[];rng=random.Random(20260930)
    for trial in (1,2):
        ordered=cases();rng.shuffle(ordered)
        for case in ordered:
            arms=ARMS[:];rng.shuffle(arms)
            schedule.extend({'case_id':case['id'],'condition':a,'trial':trial} for a in arms)
    frozen=files(RUN/'snapshots')+[RUN/'cases.json',RUN/'prediction.schema.json',RUN/'ANALYSIS_PLAN.md',Path(__file__),ROOT/'scripts/analyze_replay.py']
    frozen += files(ROOT/'evals/replay') + files(SOURCE)
    manifest={'kind':'synthetic_constrained_configuration_repair_development','frozen_at':datetime.now(timezone.utc).isoformat(),
              'model_requested':'gpt-6-astra','reasoning_effort':'high','model_snapshot':None,
              'cli_version':subprocess.check_output(['codex','--version'],text=True).strip(),
              'concurrency':3,'timeout_seconds':300,'trials':2,'seed':20260930,'schedule':schedule,
              'files_sha256':{str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in frozen},
              'prompt_sha256':{arm:digest(prompt(core(arm)).encode()) for arm in ARMS}}
    write(RUN/'manifest.json',manifest);print('Frozen 8 tasks × 3 conditions × 2 trials = 48 runs.')


def verify(cli=True):
    m=json.loads((RUN/'manifest.json').read_text())
    for name,expected in m['files_sha256'].items():
        assert digest((ROOT/name).read_bytes())==expected, 'Changed frozen file: '+name
    for arm in ARMS:assert digest(prompt(core(arm)).encode())==m['prompt_sha256'][arm]
    if cli:assert subprocess.check_output(['codex','--version'],text=True).strip()==m['cli_version']
    return m,{c['id']:c for c in json.loads((RUN/'cases.json').read_text())}


def stage_files(stage,case,arm):
    write(stage/'task.json',{'request':case['requirements'],'initial':case['initial']})
    write(stage/'model.json',case['model'])
    write(stage/'initial_trace.json',execute_system(case['initial'],composition.generate(case['model'])['cases'][0]))
    for name,source in [('system.py',ROOT/'evals/replay/system.py'),('composition.py',SOURCE/'scripts/composition.py'),
                        ('replay.py',ROOT/'evals/replay/visible_replay.py')]:shutil.copyfile(source,stage/name)
    if arm!=ARMS[0]:
        source=RUN/'snapshots'/arm
        for path in files(source):
            dest=stage/path.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)


def run_one(job,case,m):
    out=RUN/'raw-runs'/digest(job)[:20];out.mkdir(parents=True,exist_ok=True)
    if (out/'record.json').exists():return json.loads((out/'record.json').read_text())
    if (out/'started.json').exists():raise RuntimeError('Unfinished attempt; do not silently retry '+str(job))
    t0=time.monotonic();write(out/'started.json',{**job,'time':datetime.now(timezone.utc).isoformat()})
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    for scheme,url in urllib.request.getproxies().items():
        if scheme in ('http','https') and url:env.setdefault(scheme.upper()+'_PROXY',url)
    with tempfile.TemporaryDirectory(prefix='composition-replay-') as cwd:
        stage=Path(cwd);stage_files(stage,case,job['condition']);allowed={p.relative_to(stage).as_posix() for p in files(stage)}
        args=['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--sandbox','read-only','-C',cwd,
              '-m',m['model_requested'],'-c','model_reasoning_effort="'+m['reasoning_effort']+'"',
              '-c','web_search="disabled"','--enable','skip_host_skill_discovery','--json','--output-schema',str(RUN/'prediction.schema.json'),'-o',str(out/'prediction.json'),'-']
        for f in DISABLED:args+=['--disable',f]
        timed_out=False
        with (out/'events.jsonl').open('w') as stdout,(out/'stderr.txt').open('w') as stderr:
            proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
            try:proc.communicate(prompt(core(job['condition'])).encode(),timeout=m['timeout_seconds'])
            except subprocess.TimeoutExpired:
                timed_out=True;os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
        events=[]
        for line in (out/'events.jsonl').read_text().splitlines():
            try:events.append(json.loads(line))
            except ValueError:pass
        audits=[];reads=set();replays=0
        for event in events:
            item=event.get('item',{})
            if event.get('type')!='item.completed' or item.get('type') in ('agent_message','reasoning','error'):continue
            ok,read,replayed=audit(item.get('command',''),cwd,allowed) if item.get('type')=='command_execution' else (False,[],False)
            audits.append({'type':item.get('type'),'command':item.get('command','').replace(cwd,'<workspace>'),
                           'allowed':ok,'exit_code':item.get('exit_code'),'files':read,'replay':replayed})
            if ok and item.get('exit_code')==0:reads.update(read);replays+=int(replayed)
        isolation=all(a['allowed'] for a in audits)
    try:prediction=json.loads((out/'prediction.json').read_text())
    except (OSError,ValueError):prediction=None
    completed=any(e.get('type')=='turn.completed' for e in events)
    status=('isolation_unverified' if not isolation else 'input_not_read' if completed and 'task.json' not in reads else
            'completed' if proc.returncode==0 and completed and valid(prediction) else 'invalid_output' if completed else 'infrastructure_failure')
    record={**job,'status':status,'prediction':prediction,'elapsed_seconds':time.monotonic()-t0,'exit_code':proc.returncode,'timed_out':timed_out,
            'usage':next((e.get('usage') for e in reversed(events) if e.get('type')=='turn.completed'),None),
            'model_requested':m['model_requested'],'reasoning_effort':m['reasoning_effort'],'cli_version':m['cli_version'],
            'input_sha256':digest(case),'prompt_sha256':m['prompt_sha256'][job['condition']],
            'event_sha256':digest((out/'events.jsonl').read_bytes()),'stderr_sha256':digest((out/'stderr.txt').read_bytes()),
            'tool_audit':audits,'read_files':sorted(reads),'successful_replay_calls':replays}
    write(out/'record.json',record);return record


def run():
    m,cs=verify();jobs=[j for j in m['schedule'] if not (RUN/'raw-runs'/digest(j)[:20]/'record.json').exists()]
    print(json.dumps({'planned':len(m['schedule']),'pending':len(jobs)}),flush=True)
    with ThreadPoolExecutor(max_workers=m['concurrency']) as pool:
        futures=[pool.submit(run_one,j,cs[j['case_id']],m) for j in jobs]
        for n,future in enumerate(as_completed(futures),1):
            r=future.result();print(json.dumps({'finished':n,**{k:r[k] for k in ('case_id','condition','trial','status')},'seconds':round(r['elapsed_seconds'],1)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['freeze','verify','run']);a=p.parse_args().action
    if a=='freeze':freeze()
    elif a=='run':run()
    else:verify();print('Frozen protocol, candidate, inputs, runner and grader verified.')
