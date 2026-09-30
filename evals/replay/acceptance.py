"""Behavioral acceptance independent of the candidate predicate interpreter."""
from itertools import product
import math
from .system import run, OPTIONS


def failures(trace, scenario):
    errors=[]; events=trace['events']; final=events[-1]['output']; state=events[-1]['after']
    status=scenario['response_status']
    if final['status'] != status: errors.append('status')
    if final['decision'] != {'found':'answer','empty':'no_records','failed':'unknown'}[status]: errors.append('decision')
    docs=final['docs']
    expected_ids=[scenario['tenant']+'-doc'] if status=='found' else []
    if [d['id'] for d in docs] != expected_ids: errors.append('document_identity')
    if any(d['tenant']!=scenario['tenant'] for d in docs): errors.append('tenant')
    amount=scenario['amount']/100 if scenario['unit']=='cents' else scenario['amount']
    if any(not math.isclose(d['amount_usd'],amount,rel_tol=0,abs_tol=1e-9) for d in docs):errors.append('unit')
    if final['source_ids'] != expected_ids:errors.append('lineage')
    samples=final['samples']
    if len(samples)!=scenario['sample_count']:errors.append('count')
    if scenario['sampling_requirement']=='fresh' and len(set(s['call_id'] for s in samples))!=len(samples):errors.append('invocations')
    expected_amount=amount if status=='found' else 0
    if any(not math.isclose(s['value'],expected_amount,rel_tol=0,abs_tol=1e-9) for s in samples): errors.append('sample_value')
    if state['calls']!=state['charged']:errors.append('accounting')
    # Pure stages must not change any modeled resource.
    for e in events:
        if e['action'] in ('normalize','conclude') and e['before']!=e['after']:errors.append('effects')
    return sorted(set(errors))


def score(config, scenarios):
    rows=[{'input':s,'failures':failures(run(config,s),s)} for s in scenarios]
    return {'all_pass':all(not r['failures'] for r in rows),'passed':sum(not r['failures'] for r in rows),
            'total':len(rows),'failures':sorted(set(x for r in rows for x in r['failures']))}


def min_edits(initial, scenarios):
    best=len(OPTIONS)+1
    for vals in product(*OPTIONS.values()):
        config=dict(zip(OPTIONS,vals)); distance=sum(initial[k]!=v for k,v in config.items())
        if distance<best and score(config,scenarios)['all_pass']:best=distance
    if best>len(OPTIONS):raise ValueError('No acceptable configuration')
    return best
