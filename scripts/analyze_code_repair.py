#!/usr/bin/env python3
"""Publish auditable artifacts, regrade actual source and plot the frozen experiment."""
import argparse
from collections import Counter
import itertools
import json
import re
from pathlib import Path
import shutil
import statistics
from run_code_repair_eval import ROOT, RUN, ARMS, digest, verify, write
from evals.code_repair.grading import behavioral_check,structure_check
from evals.code_repair.protocol import REVIEW_PROMPT,review_input,review_valid,final_valid

PUBLIC=RUN/'results'


def behavioral_signature(value):
    return {'passed':value['passed'],'tests_run':value['tests_run'],
            'checks':[(c['test'],c['passed']) for c in value.get('checks',[])]}


def summarize(rows, cases):
    summary={}
    for arm in ARMS:
        rs=[r for r in rows if r['condition']==arm]
        ok=lambda r:r['status']=='completed' and r['acceptance']['passed']
        incidents=[r for r in rs if cases[r['case_id']]['variant']=='incident']
        normal=[r for r in rs if cases[r['case_id']]['variant']=='normal']
        reviews=[r['review'] for r in rs if r['review']['valid']]
        summary[arm]={'n':len(rs),'behavior_pass':sum(map(ok,rs)),
            'incident_pass':sum(map(ok,incidents)),'incident_n':len(incidents),
            'normal_pass':sum(map(ok,normal)),'normal_n':len(normal),
            'normal_unchanged':sum(not r['source_changed'] for r in normal),
            'structure_valid':sum(r['structure']['valid'] and r['status']=='completed' for r in rs),
            'initial_verdict_correct':sum((r.get('prediction') or {}).get('initial_verdict')==('violation' if cases[r['case_id']]['variant']=='incident' else 'satisfied') and r['status']=='completed' for r in rs),
            'review_valid':len(reviews),'review_all_covered':sum(all(c['assessment']=='covered' for c in r['prediction']['criteria']) for r in reviews),
            'review_criteria_counts':dict(Counter(c['assessment'] for r in reviews for c in r['prediction']['criteria'])),
            'tool_execution_command_runs':sum(any(re.search(r'(?:run_python|python[0-9.]*)\s+(?:\S*/)?(?:composition|check_evidence)\.py(?:\s|$)',t.get('command','')) for t in r['tool_audit']) for r in rs),
            'median_seconds':statistics.median(r['elapsed_seconds'] for r in rs),
            'status_counts':dict(Counter(r['status'] for r in rs))}
        for label,items in [('repair',rs),('review',[r['review'] for r in rs])]:
            summary[arm][label+'_usage_missing']=sum(r.get('usage') is None for r in items)
            summary[arm][label+'_tokens']={k:sum((r.get('usage') or {}).get(k,0) for r in items) for k in ('input_tokens','cached_input_tokens','output_tokens')}
    differences={}
    families=sorted({c['family'] for c in cases.values()})
    for left,right in [('B_tools','A_basic'),('C_v4','B_tools'),('C_v4','A_basic')]:
        ds=[]
        for family in families:
            def mean(arm):
                vals=[r['status']=='completed' and r['acceptance']['passed'] for r in rows if r['condition']==arm and cases[r['case_id']]['family']==family]
                return statistics.mean(vals)
            ds.append(mean(left)-mean(right))
        boot=sorted(statistics.mean(x) for x in itertools.product(ds,repeat=3))
        differences[left+' minus '+right]={'family_order':families,'family_differences':ds,'mean':statistics.mean(ds),
            'bootstrap_27_range':[boot[0],boot[-1]],'warning':'Only 3 development families; full 27-resample range, not a reliable population confidence interval.'}
    return {'conditions':summary,'paired_family_differences':differences,'planned_runs':36,'development_only':True}


def publish(import_raw=False):
    m,cs=verify(check_cli=False)
    rows=[]
    for job in m['schedule']:
        key=digest(job)[:20];dest=PUBLIC/key;raw=RUN/'raw-runs'/key
        if import_raw:
            assert (raw/'review/record.json').exists(), 'Review missing: '+key
            dest.mkdir(parents=True,exist_ok=True)
            shutil.copytree(raw/'artifacts',dest/'artifacts',dirs_exist_ok=True)
            shutil.copyfile(raw/'patch.diff',dest/'patch.diff')
            r=json.loads((raw/'record.json').read_text());r['review']=json.loads((raw/'review/record.json').read_text())
            write(dest/'record.json',r)
        r=json.loads((dest/'record.json').read_text())
        assert all(r[k]==v for k,v in job.items())
        assert r['case_sha256']==digest(cs[r['case_id']]) and r['prompt_sha256']==m['prompt_sha256'][r['condition']]
        for name,h in r['artifacts'].items():
            path=dest/'artifacts'/name
            assert path.resolve().is_relative_to((dest/'artifacts').resolve()) and digest(path.read_bytes())==h
        app=(dest/'artifacts/app.py').read_text() if (dest/'artifacts/app.py').exists() else ''
        try:model=json.loads((dest/'artifacts/model.json').read_text())
        except (OSError,ValueError):model=None
        assert r['source_changed']==(app!=cs[r['case_id']]['source'])
        if r['status']=='completed':
            assert r['completed'] and r['exit_code']==0 and not r['timed_out'] and final_valid(r['prediction'])
            assert not r['protected_changes'] and not r['outside_changes'] and not r['artifact_errors']
        packet=review_input(cs[r['case_id']],cs[r['case_id']]['source'],app,model,r['structure'],r['acceptance'])
        assert r['review']['input_sha256']==digest((REVIEW_PROMPT+'\n'+json.dumps(packet,ensure_ascii=False)).encode())
        if r['review']['valid']:
            assert review_valid(r['review']['prediction']) and not r['review']['tool_items']
            assert r['review']['completed'] and r['review']['exit_code']==0 and not r['review']['timed_out']
        assert structure_check(model,app)==r['structure'], 'Structure changed: '+key
        fresh=behavioral_check(cs[r['case_id']]['family'],app)
        assert behavioral_signature(fresh)==behavioral_signature(r['acceptance']), 'Behavior changed: '+key
        rows.append(r)
    summary=summarize(rows,cs);write(RUN/'summary.json',summary)
    plot(rows,summary,cs)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def plot(rows,summary,cases):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#687582','#23957d','#7653b3'];labels=['Basic','+ Contract tools','+ v4 guidance']
    fig,axes=plt.subplots(1,2,figsize=(12,4.6))
    for i,arm in enumerate(ARMS):
        s=summary['conditions'][arm];vals=[s['behavior_pass'],s['structure_valid'],s['review_all_covered']]
        bars=axes[0].bar([x+(i-1)*.25 for x in range(3)],vals,width=.24,color=colors[i],label=labels[i])
        axes[0].bar_label(bars,labels=[f'{v}/12' for v in vals],fontsize=8)
        ordered=sorted([r for r in rows if r['condition']==arm],key=lambda r:(r['case_id'],r['trial']))
        values=[r['status']=='completed' and r['acceptance']['passed'] for r in ordered]
        axes[1].plot(range(1,13),[sum(values[:n])/n for n in range(1,13)],marker=['o','s','^'][i],color=colors[i],label=labels[i],alpha=.8)
    axes[0].set_xticks(range(3),['Behavior','Model syntax','Blind review\nall covered']);axes[0].set_ylim(0,14);axes[0].set_ylabel('Runs / 12 per condition')
    axes[0].set_title('Execution and auxiliary modeling checks');axes[0].legend(fontsize=8,loc='lower left')
    axes[1].set_ylim(-.03,1.08);axes[1].set_xticks([2,4,6,8,10,12],['c01','c02','c03','c04','c05','c06']);axes[1].set_ylabel('Cumulative behavior pass rate');axes[1].set_title('Fixed case order; 2 repeats each');axes[1].legend(fontsize=8)
    fig.suptitle('Autonomous code repair · 3 runtime families · development only',fontsize=13)
    fig.text(.5,.015,'36 repairs; same model-output requirement. Blind review is auxiliary, not proof or a production benchmark.',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.05,1,.94))
    assets=ROOT/'assets';assets.mkdir(exist_ok=True)
    fig.savefig(assets/'code-repair-results.png',dpi=160);fig.savefig(assets/'code-repair-results.svg');plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['import','publish']);args=p.parse_args()
    publish(args.action=='import')
