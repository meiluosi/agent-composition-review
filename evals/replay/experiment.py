"""Frozen development tasks for constrained, executed configuration repair."""
from copy import deepcopy
from .make_model import model
from .defaults import DEFAULT


def cases():
    defects=[{'cache_scope':'query','retry_accounting':'success_only'},
             {'error_mode':'empty','lineage_mode':'drop'},
             {'unit_mode':'identity','sample_mode':'reuse'},
             {'unit_mode':'identity','retry_accounting':'success_only'},
             {'cache_scope':'query','error_mode':'empty','lineage_mode':'drop'},
             {'unit_mode':'identity','lineage_mode':'drop','retry_accounting':'success_only'},
             {}, {'sample_mode':'reuse'}]
    out=[]
    for i,changes in enumerate(defects,1):
        m=model()
        if i==8:m['probes']['base']['sampling_requirement']='reuse_allowed'
        out.append({'id':f'r{i:02d}', 'initial':{**DEFAULT,**changes},'model':deepcopy(m),
                    'requirements':'Repair the smallest number of configuration fields so all declared input domains meet the contracts. '
                    'Preserve already valid choices. A found response must use documents for the requesting tenant; failures stay failed, '
                    'amounts use USD, source IDs survive, all actual attempts are charged. Fresh sampling requires separate invocations '
                    'when sampling_requirement=fresh; reuse_allowed permits either repeated or fresh draws. '
                    'No external services or real random model calls are used in this local fixture.'})
    return out


def extra_scenarios(m):
    # New numeric/scope combinations, not independent task holdout.
    result=[]
    for amount,tenant in [(0,'beta'),(-500,'gamma'),(12345,'delta')]:
        for status in ('found','empty','failed'):
            result.append({**m['probes']['base'],'amount':amount,'tenant':tenant,'response_status':status,
                           'warm_cache':True,'retry':True,'sample_count':4})
    return result
