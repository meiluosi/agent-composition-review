"""Small executable retrieval agent with deterministic fake external services.

Configuration options model plausible cache, error, lineage and normalization policies.
No networking or model inference occurs here. Trace payloads come from executed code.
"""
from copy import deepcopy

OPTIONS = {'cache_scope':['query','tenant_query'], 'error_mode':['empty','tagged'],
           'unit_mode':['identity','convert'], 'sample_mode':['reuse','fresh'],
           'lineage_mode':['drop','preserve'], 'retry_accounting':['success_only','all_calls']}


def run(config, scenario):
    if set(config) != set(OPTIONS) or any(config[k] not in OPTIONS[k] for k in config):
        raise ValueError('Unsupported configuration')
    state = {'calls':0, 'charged':0, 'cache':{}}
    events = []
    request = deepcopy(scenario)
    request['query'] = 'quarter revenue'
    request['allowed_tenants'] = [request['tenant']]
    def execute(action, data, fn):
        before = deepcopy(state); output = fn(deepcopy(data))
        event = {'id':f'e{len(events)+1}', 'kind':'action', 'action':action,
                 'input':deepcopy(data), 'output':deepcopy(output), 'before':before,
                 'after':deepcopy(state), 'parents':[events[-1]['id']] if events else [],
                 'source':'deterministic-retrieval-adapter:'+action}
        events.append(event); return output

    def retrieve(q):
        def cache_key(tenant):
            return tenant+'|'+q['query'] if config['cache_scope']=='tenant_query' else q['query']
        if q['warm_cache']:
            state['cache'][cache_key('other')] = {'status':'found','docs':[{'id':'other-doc','tenant':'other','amount':900,'unit':q['unit']} ]}
        key = cache_key(q['tenant'])
        if key in state['cache']:
            result = deepcopy(state['cache'][key])
        else:
            if q['retry']:
                state['calls'] += 1
                if config['retry_accounting']=='all_calls': state['charged'] += 1
            state['calls'] += 1; state['charged'] += 1
            status = q['response_status']
            result = {'status':status,'docs':[]}
            if status == 'found':
                result['docs'] = [{'id':q['tenant']+'-doc', 'tenant':q['tenant'], 'amount':q['amount'], 'unit':q['unit']}]
            if status == 'failed' and config['error_mode']=='empty': result['status'] = 'empty'
            state['cache'][key] = deepcopy(result)
        return {**q, **result}

    def normalize(r):
        docs = []
        for doc in r['docs']:
            amount = doc['amount']
            if config['unit_mode']=='convert' and doc['unit']=='cents': amount /= 100
            docs.append({**doc, 'amount_usd':amount})
        return {**r,'docs':docs}

    def aggregate(r):
        samples = []
        # Actual fake-sampler invocations count even if values happen to coincide.
        first = None
        for _ in range(r['sample_count']):
            if first is None or config['sample_mode']=='fresh':
                state['calls'] += 1; state['charged'] += 1
                first = {'call_id':f'call-{state["calls"]}', 'value':sum(d['amount_usd'] for d in r['docs'])}
            samples.append(deepcopy(first))
        return {**r, 'samples':samples, 'source_ids':[d['id'] for d in r['docs']] if config['lineage_mode']=='preserve' else []}

    def conclude(r):
        return {**r, 'decision':{'found':'answer','empty':'no_records','failed':'unknown'}[r['status']]}
    value = request
    for name, fn in [('retrieve',retrieve),('normalize',normalize),('aggregate',aggregate),('conclude',conclude)]:
        value = execute(name, value, fn)
    return {'complete':True, 'events':events}
