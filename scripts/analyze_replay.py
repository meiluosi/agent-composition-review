#!/usr/bin/env python3
"""Verify structured predictions by actually replaying configurations and publish figures."""
import argparse
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evals.replay.acceptance import score, min_edits
from evals.replay.experiment import extra_scenarios
from evals.replay.runtime import composition
from run_replay_eval import RUN, ARMS, verify, digest, write


def grade(record,case,scenarios,minimum):
    valid=record['status']=='completed';p=record.get('prediction')
    acceptance=score(p['configuration'],scenarios) if valid else {'all_pass':False,'passed':0,'total':len(scenarios),'failures':['invalid_run']}
    edits=sum(case['initial'][k]!=v for k,v in p['configuration'].items()) if valid else None
    initial=score(case['initial'],scenarios)
    return {'behavior_pass':acceptance['all_pass'],'minimal_repair':acceptance['all_pass'] and edits==minimum,
            'acceptance':acceptance,'edits':edits,'minimum_edits':minimum,
            'initial_verdict_correct':valid and p['initial_verdict']==('satisfied' if initial['all_pass'] else 'violation'),
            'healthy_unnecessary_change':(valid and edits>0) if initial['all_pass'] else None,
            'verification_claim_supported':valid and (not p['verified'] or record['successful_replay_calls']>0)}


def summarize(rows):
    import numpy as np
    result={}
    for arm in ARMS:
        selected=[r for r in rows if r['condition']==arm]
        healthy=[r for r in selected if r['score']['healthy_unnecessary_change'] is not None]
        result[arm]={'runs':len(selected),'valid':sum(r['status']=='completed' for r in selected),
                     'behavior_pass':sum(r['score']['behavior_pass'] for r in selected),
                     'minimal_repair':sum(r['score']['minimal_repair'] for r in selected),
                     'initial_verdict_correct':sum(r['score']['initial_verdict_correct'] for r in selected),
                     'healthy_unnecessary_change':sum(r['score']['healthy_unnecessary_change'] for r in healthy),
                     'healthy_runs':len(healthy),'replay_runs':sum(r['successful_replay_calls']>0 for r in selected),
                     'input_tokens':sum((r.get('usage') or {}).get('input_tokens',0) for r in selected),
                     'output_tokens':sum((r.get('usage') or {}).get('output_tokens',0) for r in selected),
                     'median_seconds':statistics.median(r['elapsed_seconds'] for r in selected)}
    ids=sorted({r['case_id'] for r in rows});rng=np.random.default_rng(20260930);indices=rng.integers(0,len(ids),size=(10000,len(ids)))
    def per_case(arm):return np.array([statistics.mean(float(r['score']['behavior_pass']) for r in rows if r['condition']==arm and r['case_id']==id) for id in ids])
    differences={}
    for arm in ARMS[1:]:
        delta=per_case(arm)-per_case(ARMS[0]);ci=np.quantile(delta[indices].mean(axis=1),[0.025,0.975])
        differences[arm]={'difference':float(delta.mean()),'ci95':ci.tolist()}
    return {'conditions':result,'paired_behavior_differences_vs_no_skill':differences,'case_order':ids,
            'limitations':['Eight authored variants of one system; bootstrap is not cross-system uncertainty.',
                          'Matched tools, supplied IR, constrained configuration choices; no autonomous code repair or natural triggering.',
                          'No changes or retries selected by model outcome.','A [0,0] interval is empirical degeneracy, not certainty about future performance.']}


def publish():
    manifest,cases=verify(cli=False);public=RUN/'predictions.json'
    if public.exists():
        records=json.loads(public.read_text())
        assert len(records)==len(manifest['schedule'])
    else:
        records=[]
        for job in manifest['schedule']:
            path=RUN/'raw-runs'/digest(job)[:20]/'record.json'
            if not path.exists():raise SystemExit('Missing planned run: '+str(job))
            records.append(json.loads(path.read_text()))
    actual=[{k:r[k] for k in ('case_id','condition','trial')} for r in records]
    assert actual==manifest['schedule'],'Predictions must match frozen schedule exactly'
    scenarios={id:composition.generate(c['model'])['cases']+extra_scenarios(c['model']) for id,c in cases.items()}
    minima={id:min_edits(c['initial'],scenarios[id]) for id,c in cases.items()}
    for r in records:
        assert r['input_sha256']==digest(cases[r['case_id']])
        assert r['prompt_sha256']==manifest['prompt_sha256'][r['condition']]
        newscore=grade(r,cases[r['case_id']],scenarios[r['case_id']],minima[r['case_id']])
        if 'score' in r:assert r['score']==newscore,'Published score not reproducible'
        r['score']=newscore
    write(public,records);summary=summarize(records);write(RUN/'summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#687782','#de9d32','#317d88'];labels=['No skill','V3','V4'];n=16
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    values=[summary['conditions'][a]['behavior_pass']/n*100 for a in ARMS]
    axes[0].bar(labels,values,color=colors);axes[0].set(ylim=(0,115),ylabel='Runs passing all 57 inputs (%)',title='Executed acceptance (16 runs / arm)')
    for i,v in enumerate(values):axes[0].text(i,v+2,f'{v:.1f}%',ha='center')
    costs=[summary['conditions'][a]['input_tokens']/1000 for a in ARMS]
    axes[1].bar(labels,costs,color=colors);axes[1].set(ylabel='Input tokens (thousands)',title='Observed context cost')
    fig.suptitle('Synthetic configuration repair: 8 tasks, 2 repeats; matched tools',fontsize=11)
    assets=ROOT/'docs/assets'
    for ext in ('png','svg'):fig.savefig(assets/f'replay-v4-overview.{ext}',dpi=160)
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4),layout='constrained')
    for arm,label,color,style in zip(ARMS,labels,colors,['-','--',':']):
        taskmeans=[statistics.mean(float(r['score']['behavior_pass']) for r in records if r['condition']==arm and r['case_id']==id) for id in summary['case_order']]
        ax.plot(range(1,9),[statistics.mean(taskmeans[:i])*100 for i in range(1,9)],label=label,color=color,linestyle=style,marker='o')
    ax.set(ylim=(-5,105),xticks=range(1,9),xlabel='Tasks in frozen r01–r08 order',ylabel='Cumulative acceptance (%)',title='Not a learning curve; same-system development variants');ax.legend()
    for ext in ('png','svg'):fig.savefig(assets/f'replay-v4-cumulative.{ext}',dpi=160)
    plt.close(fig)
    lines=['# v4 配置修复对照结果','',f'完成 {len(records)}/48 次计划尝试。结果由实际执行配置后计算，每次验收 57 个输入；不依赖模型自报 verified。','',
           '| 条件 | 有效运行 | 全部验收通过 | 最小修复通过 | 正常例不必要改动 | 实际调用回放 | 输入 token | 时间中位数 |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm in ARMS:
        s=summary['conditions'][arm]
        lines.append(f'| {arm} | {s["valid"]}/16 | {s["behavior_pass"]}/16 | {s["minimal_repair"]}/16 | {s["healthy_unnecessary_change"]}/{s["healthy_runs"]} | {s["replay_runs"]}/16 | {s["input_tokens"]:,} | {s["median_seconds"]:.1f}s |')
    lines+=['','相对无 Skill 的首指标差值与任务配对 bootstrap：']
    for arm,d in summary['paired_behavior_differences_vs_no_skill'].items():
        lines.append(f'- {arm}: {100*d["difference"]:+.1f}pp，95% 区间 [{100*d["ci95"][0]:+.1f}, {100*d["ci95"][1]:+.1f}]pp。')
    lines+=['','仅有同一自建系统的八个开发变体；不能证明泛化。共同工具和共同结构输入已提供较多帮助，结果只反映指令的额外价值。全部成功时存在天花板，[0,0] 不意味着未来差异确定为零。',
            '','本轮保留 v4 为可运行候选，默认包与个人安装不晋升。是否适合真实任务还需自然触发、自主建模及不同系统的独立验证。',
            '','[冻结方案](ANALYSIS_PLAN.md) · [清单](manifest.json) · [逐次预测](predictions.json) · [汇总](summary.json)',
            '','复核和重画：`python scripts/analyze_replay.py publish`，不调用模型。']
    (RUN/'REPORT.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['publish']);parser.parse_args();publish()
