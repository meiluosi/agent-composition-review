#!/usr/bin/env python3
"""Generate contract obligations and check recorded executions; Python standard library only."""
import argparse
import hashlib
import json
import itertools
from pathlib import Path
import sys


class Invalid(ValueError):
    pass


class Missing(KeyError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def at(value, path):
    for part in path.split('.') if path else []:
        if not isinstance(value, dict) or part not in value:
            raise Missing(path)
        value = value[part]
    return value


def expression(expr, ctx):
    if not isinstance(expr, dict) or len(expr) != 1:
        raise Invalid('Expression must have exactly one operator')
    op, arg = next(iter(expr.items()))
    if op == 'literal': return arg
    if op == 'path': return at(ctx, arg)
    if op == 'count': return len(expression(arg, ctx))
    if op == 'scale': return [v * arg['factor'] for v in expression(arg['value'], ctx)]
    if op == 'pluck':
        return [at(v, arg['field']) for v in expression(arg['value'], ctx)]
    raise Invalid('Unknown expression: ' + op)


def predicate(p, ctx):
    op = p['op']; args = p['args']
    if op == 'and': return all(predicate(x, ctx) for x in args)
    if op == 'or': return any(predicate(x, ctx) for x in args)
    a = expression(args[0], ctx)
    if op == 'unique': return len({digest(x) for x in a}) == len(a)
    b = expression(args[1], ctx)
    if op == 'eq': return type(a) is type(b) and a == b
    if op == 'ne': return not (type(a) is type(b) and a == b)
    if op == 'le': return a <= b
    if op == 'ge': return a >= b
    if op == 'contains': return b in a
    if op == 'subset': return all(x in b for x in a)
    raise Invalid('Unknown predicate: ' + op)


def validate_expr(e):
    if not isinstance(e, dict) or len(e) != 1: raise Invalid('Invalid expression')
    op, arg = next(iter(e.items()))
    if op == 'literal': return
    if op == 'path':
        if not isinstance(arg, str) or not arg: raise Invalid('Empty path')
    elif op == 'count': validate_expr(arg)
    elif op == 'scale':
        validate_expr(arg['value'])
        if type(arg['factor']) not in (int,float): raise Invalid('Scale factor must be numeric')
    elif op == 'pluck':
        validate_expr(arg['value'])
        if not isinstance(arg['field'], str) or not arg['field']: raise Invalid('Empty pluck field')
    else: raise Invalid('Unknown expression: ' + op)


def validate_pred(p):
    op, args = p['op'], p['args']
    if not isinstance(args, list): raise Invalid('Predicate args must be a list')
    if op in ('and', 'or'):
        if not args: raise Invalid('Empty logical predicate')
        for a in args: validate_pred(a)
    else:
        if op not in ('eq', 'ne', 'le', 'ge', 'contains', 'subset', 'unique'): raise Invalid('Unknown predicate')
        if len(args) != (1 if op == 'unique' else 2): raise Invalid('Wrong predicate arity')
        for a in args: validate_expr(a)


def validate(model):
    if model['schema_version'] != 1: raise Invalid('Unsupported schema_version')
    if not isinstance(model['types'], dict) or not model['types']: raise Invalid('Missing types')
    for fields in model['types'].values():
        if not isinstance(fields, dict): raise Invalid('Type must map field paths to constraints')
        for path, spec in fields.items():
            if not path or spec['type'] not in ('string','number','boolean','object','array','null'):
                raise Invalid('Invalid field type')
            if 'enum' in spec and (not isinstance(spec['enum'], list) or not spec['enum']): raise Invalid('Empty enum')
    actions = model['actions']
    if not isinstance(actions, list) or not actions: raise Invalid('Missing actions')
    ids = [a['id'] for a in actions]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids): raise Invalid('Duplicate/invalid action id')
    for a in actions:
        if a['input_type'] not in model['types'] or a['output_type'] not in model['types']: raise Invalid('Unknown type')
        if not isinstance(a['writes'], list) or not all(isinstance(x,str) and x for x in a['writes']): raise Invalid('Invalid writes')
        if not isinstance(a['reads'], list): raise Invalid('Invalid reads')
        for field in ('pre', 'post'):
            for rule in a[field]:
                if not rule['id'] or not rule['requirement']: raise Invalid('Missing contract provenance')
                validate_pred(rule['must'])
                if 'when' in rule: validate_pred(rule['when'])
        ruleids = [r['id'] for k in ('pre','post') for r in a[k]]
        if len(set(ruleids)) != len(ruleids): raise Invalid('Duplicate rule id')
    for edge in model['edges']:
        if edge['from'] not in ids or edge['to'] not in ids: raise Invalid('Unknown edge endpoint')
        if not edge['bindings']: raise Invalid('Empty edge bindings')
        for binding in edge['bindings']:
            if not all(isinstance(binding[k], str) and binding[k] for k in ('output','input')): raise Invalid('Invalid binding')
    required = model['required_actions']
    if not isinstance(required, list) or not set(required) <= set(ids): raise Invalid('Unknown required action')
    return model


def plan(model):
    validate(model)
    obligations = []
    for a in model['actions']:
        for side in ('input','output'):
            obligations.append({'id': f'{a["id"]}.{side}', 'kind':'type', 'action':a['id'], 'side':side,
                                'type':a[side+'_type'], 'probe':'required fields, wrong types, enum boundaries'})
        obligations.append({'id':a['id']+'.effects', 'kind':'effects', 'action':a['id'],
                            'writes':a['writes'], 'probe':'compare complete before/after snapshots, include failure paths'})
        for side in ('pre','post'):
            for r in a[side]:
                obligations.append({'id':a['id']+'.'+side+'.'+r['id'], 'kind':'predicate', 'action':a['id'],
                                    'rule':r, 'probe':'one satisfying and one violating witness, plus missing evidence'})
    for i, edge in enumerate(model['edges']):
        obligations.append({'id':f'edge.{i}', 'kind':'edge', **edge,
                            'probe':'execute producer then consumer; preserve bindings and actual dependency'})
    obligations.append({'id':'trace.complete', 'kind':'completion', 'required_actions':model['required_actions'],
                        'probe':'complete successful path and truncated/failed path'})
    return {'schema_version':1, 'model_sha256':digest(model), 'obligations':obligations,
            'limits':['Finite recorded executions only; no proof of semantic truth, independence or causality.',
                      'Reads are declared, not observed. State checks cover supplied top-level keys only.',
                      'Probe descriptions require a system adapter; they are not executed tests by themselves.']}


def generate(model, limit=1024):
    """Expand declared finite input domains, not arbitrary code or inferred requirements."""
    validate(model)
    probes = model.get('probes')
    if not isinstance(probes,dict) or not isinstance(probes.get('base'),dict) or not isinstance(probes.get('axes'),dict):
        raise Invalid('probes requires base and axes objects')
    axes = probes['axes']; size = 1
    for name, values in axes.items():
        if not isinstance(values,list) or not values: raise Invalid('Each probe axis must be nonempty')
        if name not in probes['base']: raise Invalid('Axis must name a base input field')
        size *= len(values)
    if size > limit: raise Invalid(f'{size} probes exceed explicit expansion limit {limit}')
    cases = [{**probes['base'], **dict(zip(axes, values))} for values in itertools.product(*axes.values())]
    return {'schema_version':1,'model_sha256':digest(model),'cases':cases,
            'scope':'Cartesian coverage of declared domains; unseen domains and requirements are not covered'}


def type_ok(value, fields):
    for path, spec in fields.items():
        try: v = at(value, path)
        except Missing: return False
        t = spec['type']
        ok = {'string':isinstance(v,str), 'number':type(v) in (int,float), 'boolean':type(v) is bool,
              'object':isinstance(v,dict), 'array':isinstance(v,list), 'null':v is None}[t]
        if not ok or ('enum' in spec and not any(type(v) is type(x) and v == x for x in spec['enum'])): return False
    return True


def check(model, trace):
    obligations = plan(model)['obligations']; results = []; seen = {}; previous = None
    def add(oid, eid, status, detail):
        results.append({'obligation_id':oid, 'action_event_id':eid, 'status':status, 'detail':detail})
    events = trace.get('events', [])
    if not isinstance(events, list): raise Invalid('events must be a list')
    actions = {a['id']:a for a in model['actions']}
    executed = set()
    for e in events:
        if e.get('kind') != 'action': continue  # plans and observations never execute transitions
        eid = e.get('id')
        if not isinstance(eid,str) or not eid or eid in seen: raise Invalid('Duplicate/missing event id')
        aid = e.get('action')
        if aid not in actions: raise Invalid('Unknown executed action')
        if not e.get('source'): raise Invalid('Execution requires source provenance')
        parents = e.get('parents')
        if not isinstance(parents,list) or not all(isinstance(x,str) and x in seen for x in parents):
            raise Invalid('Parents must refer to earlier action events')
        executed.add(aid)
        for ob in obligations:
            if ob.get('action') != aid: continue
            try:
                if ob['kind'] == 'type':
                    value = at(e, ob['side']); ok = type_ok(value, model['types'][ob['type']])
                elif ob['kind'] == 'effects':
                    before, after = at(e,'before'), at(e,'after')
                    if not isinstance(before,dict) or not isinstance(after,dict): raise Invalid('State must be an object')
                    changed = {k for k in before.keys() | after.keys() if k not in before or k not in after or before[k] != after[k]}
                    ok = changed <= set(ob['writes'])
                else:
                    rule = ob['rule']
                    if 'when' in rule and not predicate(rule['when'], e):
                        add(ob['id'],eid,'not_applicable','Guard false on recorded values'); continue
                    ok = predicate(rule['must'], e)
                add(ob['id'],eid,'pass' if ok else 'fail','Checked recorded action values')
            except (Missing, TypeError, ValueError, IndexError) as exc:
                add(ob['id'],eid,'unknown','Missing or incomparable evidence: '+str(exc))
        # This trace format models one sequential state stream, not concurrent partial orders.
        if previous is not None:
            status = 'unknown' if 'after' not in previous or 'before' not in e else ('pass' if previous['after'] == e['before'] else 'fail')
            add('trace.state_continuity',eid,status,'Adjacent global snapshots must agree')
        for ob in obligations:
            if ob['kind'] != 'edge' or ob['to'] != aid: continue
            producers = [seen[p] for p in parents if seen[p]['action'] == ob['from']]
            if not producers:
                add(ob['id'],eid,'fail','Required producer absent from actual dependencies'); continue
            try:
                ok = any(all(at(p['output'], b['output']) == at(e['input'], b['input']) for b in ob['bindings']) for p in producers)
                add(ob['id'],eid,'pass' if ok else 'fail','Compared producer output with consumer input')
            except (Missing, KeyError, TypeError): add(ob['id'],eid,'unknown','Missing dependency payload')
        seen[eid] = e; previous = e
    complete = trace.get('complete') is True
    add('trace.complete',None,('pass' if set(model['required_actions']) <= executed else 'fail') if complete else 'unknown',
        'Required actions observed' if complete else 'Trace not declared complete')
    counts = {s:sum(x['status']==s for x in results) for s in ('pass','fail','unknown','not_applicable')}
    return {'schema_version':1, 'model_sha256':digest(model), 'trace_sha256':digest(trace),
            'verdict':'violation' if counts['fail'] else 'insufficient' if counts['unknown'] else 'satisfied',
            'counts':counts, 'checks':results,
            'causal_status':'untested', 'scope':'Recorded execution under supplied contracts and instrumentation'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['validate','plan','generate','check']); p.add_argument('model',type=Path)
    p.add_argument('trace',type=Path,nargs='?')
    p.add_argument('--limit',type=int,default=1024,help='Maximum generated probes; explicit expansion budget')
    args = p.parse_args()
    try:
        model = json.loads(args.model.read_text()); validate(model)
        if args.action == 'validate': result = {'valid':True, 'model_sha256':digest(model)}
        elif args.action == 'plan': result = plan(model)
        elif args.action == 'generate': result = generate(model, args.limit)
        else:
            if args.trace is None: raise Invalid('check requires a trace file')
            result = check(model, json.loads(args.trace.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('verdict') in ('violation','insufficient') else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False)); return 2


if __name__ == '__main__': sys.exit(main())
