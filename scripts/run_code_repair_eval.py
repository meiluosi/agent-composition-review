#!/usr/bin/env python3
"""Freeze and run autonomous model construction plus actual source repairs."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import difflib
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import random
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evals.external.core import digest
from evals.code_repair.catalog import cases
from evals.code_repair.smoke import SMOKES
from evals.code_repair.protocol import FINAL_SCHEMA,REVIEW_SCHEMA,prompt,final_valid
from evals.code_repair.grading import behavioral_check,structure_check

RUN=ROOT/'evals/iterations/code-repair-2026-09-30';SOURCE=ROOT/'candidates/v4/agent-composition-review'
ARMS=['A_basic','B_tools','C_v4']
DISABLED=['plugins','apps','multi_agent','browser_use','computer_use','image_generation','view_image','skill_search','hooks','unbounded_connection_retries']


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def files(root):return sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')


def core(arm):return (RUN/'snapshots/C_v4/SKILL.md').read_text() if arm=='C_v4' else None


def freeze():
    if (RUN/'manifest.json').exists():raise SystemExit('Already frozen; use another iteration for changes')
    for arm in ARMS[1:]:
        source_files=files(SOURCE) if arm=='C_v4' else [SOURCE/'scripts/composition.py',SOURCE/'scripts/check_evidence.py']
        for path in source_files:
            dest=RUN/'snapshots'/arm/path.relative_to(SOURCE);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
        shutil.copyfile(ROOT/'evals/code_repair/TOOLS.md',RUN/'snapshots'/arm/'TOOLS.md')
    cs=cases()
    for c in cs:c['source']= (ROOT/c['source_file']).read_text()
    write(RUN/'cases.json',cs);write(RUN/'prediction.schema.json',FINAL_SCHEMA);write(RUN/'review.schema.json',REVIEW_SCHEMA)
    jobs=[];rng=random.Random(202609301)
    for trial in (1,2):
        ordered=cs[:];rng.shuffle(ordered)
        for c in ordered:
            arms=ARMS[:];rng.shuffle(arms)
            jobs.extend({'case_id':c['case_id'],'condition':a,'trial':trial} for a in arms)
    frozen=files(RUN/'snapshots')+files(ROOT/'evals/code_repair')
    frozen += [RUN/'cases.json',RUN/'ANALYSIS_PLAN.md',RUN/'prediction.schema.json',RUN/'review.schema.json',
               Path(__file__),ROOT/'scripts/review_code_models.py',ROOT/'scripts/analyze_code_repair.py',
               SOURCE/'scripts/composition.py']
    m={'kind':'cross_runtime_autonomous_modeling_development','frozen_at':datetime.now(timezone.utc).isoformat(),
       'model_requested':'gpt-6-astra','reasoning_effort':'high','model_snapshot':None,
       'cli_version':subprocess.check_output(['codex','--version'],text=True).strip(),
       'python_version':platform.python_version(),'sqlite_version':sqlite3.sqlite_version,'execution_platform':platform.system(),
       'langgraph_version':version('langgraph'),
       'concurrency':3,'timeout_seconds':480,'review_timeout_seconds':180,'trials':2,'seed':202609301,
       'schedule':jobs,'files_sha256':{str(p.relative_to(ROOT)):digest(p.read_bytes()) for p in frozen},
       'prompt_sha256':{a:digest(prompt(core(a)).encode()) for a in ARMS}}
    write(RUN/'manifest.json',m);print('Frozen 36 repair attempts and at most 36 blinded model reviews.')


def verify(check_cli=True):
    m=json.loads((RUN/'manifest.json').read_text())
    for path,expected in m['files_sha256'].items():assert digest((ROOT/path).read_bytes())==expected,'Changed frozen file: '+path
    for a in ARMS:assert digest(prompt(core(a)).encode())==m['prompt_sha256'][a]
    if check_cli:assert subprocess.check_output(['codex','--version'],text=True).strip()==m['cli_version']
    return m,{c['case_id']:c for c in json.loads((RUN/'cases.json').read_text())}


def stage_task(stage,case,arm):
    (stage/'app.py').write_text(case['source']);(stage/'original.py').write_text(case['source'])
    (stage/'task.md').write_text(case['request']);(stage/'smoke.py').write_text(SMOKES[case['family']])
    shutil.copyfile(ROOT/'evals/code_repair/MODEL_FORMAT.md',stage/'MODEL_FORMAT.md')
    (stage/'run_python').write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -B "$@"\n');(stage/'run_python').chmod(0o755)
    if arm!='A_basic':
        source=RUN/'snapshots'/arm
        for path in files(source):
            dest=stage/path.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
    return {p.relative_to(stage).as_posix():digest(p.read_bytes()) for p in files(stage) if p.name!='app.py'}


def environment():
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
    for scheme,url in urllib.request.getproxies().items():
        if scheme in ('http','https') and url:env.setdefault(scheme.upper()+'_PROXY',url)
    return env


def cli_args(cwd,m,output,schema,sandbox='workspace-write'):
    args=['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--sandbox',sandbox,'-C',str(cwd),
          '-m',m['model_requested'],'-c','model_reasoning_effort="'+m['reasoning_effort']+'"','-c','approval_policy="never"',
          '-c','web_search="disabled"','--enable','skip_host_skill_discovery','--json','--output-schema',str(schema),'-o',str(output),'-']
    for f in DISABLED:args+=['--disable',f]
    return args


def invoke(args,prompt_text,out,timeout):
    t0=time.monotonic();timed_out=False
    with (out/'events.jsonl').open('w') as stdout,(out/'stderr.txt').open('w') as stderr:
        proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,env=environment(),start_new_session=True)
        try:proc.communicate(prompt_text.encode(),timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
    events=[]
    for line in (out/'events.jsonl').read_text().splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    return {'exit_code':proc.returncode,'timed_out':timed_out,'elapsed_seconds':time.monotonic()-t0,
            'usage':next((e.get('usage') for e in reversed(events) if e.get('type')=='turn.completed'),None),
            'completed':any(e.get('type')=='turn.completed' for e in events),'events':events}


def run_one(job,case,m):
    out=RUN/'raw-runs'/digest(job)[:20];out.mkdir(parents=True,exist_ok=True)
    if (out/'record.json').exists():return json.loads((out/'record.json').read_text())
    if (out/'started.json').exists():raise RuntimeError('Unfinished previous attempt; no silent retry: '+str(job))
    write(out/'started.json',{**job,'time':datetime.now(timezone.utc).isoformat()})
    with tempfile.TemporaryDirectory(prefix='composition-code-') as d:
        stage=Path(d);protected=stage_task(stage,case,job['condition'])
        meta=invoke(cli_args(stage,m,out/'prediction.json',RUN/'prediction.schema.json'),prompt(core(job['condition'])),out,m['timeout_seconds'])
        changed=[name for name,h in protected.items() if not (stage/name).is_file() or (stage/name).is_symlink() or digest((stage/name).read_bytes())!=h]
        artifacts={};artifact_errors=[]
        for p in files(stage):
            name=p.relative_to(stage).as_posix()
            if name in protected:continue
            if p.is_symlink() or not p.resolve().is_relative_to(stage.resolve()):artifact_errors.append('Escaping symlink: '+name);continue
            if p.stat().st_size>500_000:artifact_errors.append('Oversized artifact: '+name);continue
            if p.suffix in ('.py','.json','.md','.txt'):
                dest=out/'artifacts'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);artifacts[name]=digest(p.read_bytes())
        audits=[];outside=[]
        for event in meta.pop('events'):
            item=event.get('item',{});kind=item.get('type')
            if event.get('type')!='item.completed' or kind in ('agent_message','reasoning','error','todo_list'):continue
            entry={'type':kind,'exit_code':item.get('exit_code')}
            if 'command' in item:
                command=item['command'];entry['command']=command.replace(d,'<workspace>').replace(str(ROOT),'<repo>')
                # Advisory inspection flags, not a proof of shell/Python confinement.
                entry['review_flags']=[v for v in ('../','curl ','wget ','evals/','auth.json','/Users/') if v in command]
            if 'changes' in item:
                entry['changes']=item['changes']
                for change in item['changes']:
                    path=change.get('path','')
                    if path and not (stage/path).resolve().is_relative_to(stage.resolve()):outside.append(path)
                entry=json.loads(json.dumps(entry).replace(d,'<workspace>'))
            audits.append(entry)
        source=(out/'artifacts/app.py').read_text() if (out/'artifacts/app.py').is_file() else ''
        try:model=json.loads((out/'artifacts/model.json').read_text())
        except (OSError,ValueError):model=None
    try:prediction=json.loads((out/'prediction.json').read_text())
    except (OSError,ValueError):prediction=None
    status=('workspace_violation' if changed or outside or artifact_errors else 'completed' if meta['completed'] and meta['exit_code']==0 and not meta['timed_out'] and final_valid(prediction) else 'invalid_output' if meta['completed'] else 'infrastructure_failure')
    acceptance=behavioral_check(case['family'],source) if source else {'passed':False,'tests_run':0,'checks':[],'missing_source':True}
    structure=structure_check(model,source)
    (out/'patch.diff').write_text(''.join(difflib.unified_diff(case['source'].splitlines(True),source.splitlines(True),fromfile='original.py',tofile='app.py')))
    record={**job,**meta,'status':status,'prediction':prediction,'acceptance':acceptance,'structure':structure,
            'source_changed':source!=case['source'],'artifacts':artifacts,'protected_changes':changed,'outside_changes':outside,'artifact_errors':artifact_errors,
            'tool_audit':audits,'case_sha256':digest(case),'prompt_sha256':m['prompt_sha256'][job['condition']],
            'event_sha256':digest((out/'events.jsonl').read_bytes()),'stderr_sha256':digest((out/'stderr.txt').read_bytes()),
            'model_requested':m['model_requested'],'reasoning_effort':m['reasoning_effort'],'cli_version':m['cli_version']}
    write(out/'record.json',record);return record


def run():
    m,cs=verify();jobs=[j for j in m['schedule'] if not (RUN/'raw-runs'/digest(j)[:20]/'record.json').exists()]
    print(json.dumps({'planned':36,'pending':len(jobs)}),flush=True)
    with ThreadPoolExecutor(max_workers=m['concurrency']) as pool:
        futures=[pool.submit(run_one,j,cs[j['case_id']],m) for j in jobs]
        for n,f in enumerate(as_completed(futures),1):
            r=f.result();print(json.dumps({'finished':n,**{k:r[k] for k in ('case_id','condition','trial','status')},'seconds':round(r['elapsed_seconds'],1)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['freeze','verify','run']);a=p.parse_args().action
    if a=='freeze':freeze()
    elif a=='run':run()
    else:verify();print('All frozen code-repair material verified.')
