"""Author the declared contracts and probe domains of the illustrative retrieval system."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / 'candidates/v4/agent-composition-review'


def p(path): return {'path':path}
def lit(value): return {'literal':value}
def rule(id, requirement, op, *args, when=None):
    result = {'id':id, 'requirement':requirement,'must':{'op':op,'args':list(args)}}
    if when is not None: result['when'] = when
    return result
def pluck(path, field): return {'pluck':{'value':p(path),'field':field}}


def model():
    base = {'query':{'type':'string'}, 'tenant':{'type':'string'}, 'sample_count':{'type':'number'},
            'response_status':{'type':'string','enum':['found','empty','failed']},
            'unit':{'type':'string','enum':['USD','cents']}, 'amount':{'type':'number'},
            'warm_cache':{'type':'boolean'}, 'retry':{'type':'boolean'}}
    result = {**base,'status':{'type':'string','enum':['found','empty','failed']}, 'docs':{'type':'array'}}
    evidence = {**result, 'source_ids':{'type':'array'}, 'samples':{'type':'array'}}
    decision = {**evidence,'decision':{'type':'string','enum':['answer','no_records','unknown']}}
    actions = []
    def action(id, src, dst, writes, post):
        actions.append({'id':id,'input_type':src,'output_type':dst,'reads':['calls','charged','cache'],
                        'writes':writes,'pre':[], 'post':post})
    action('retrieve','Query','Result',['cache','calls','charged'],[
        rule('status','Backend failure must stay distinguishable from successful empty retrieval.', 'eq', p('output.status'),p('input.response_status')),
        rule('tenant','Retrieved documents must belong to the requesting tenant.', 'subset',pluck('output.docs','tenant'),{'path':'input.allowed_tenants'}),
        rule('accounting','Charge every actual backend attempt including failed attempts.', 'eq',p('after.calls'),p('after.charged'))])
    # Input contains explicit allowed scope, rather than encoding singleton construction in the DSL.
    base['allowed_tenants']={'type':'array'}
    for fields in (result,evidence,decision): fields['allowed_tenants']={'type':'array'}
    action('normalize','Result','Result',[],[
        rule('cents','Normalize cents into USD before aggregation.', 'eq',pluck('output.docs','amount_usd'),{'scale':{'value':pluck('input.docs','amount'),'factor':0.01}},when={'op':'eq','args':[p('input.unit'),lit('cents')]}),
        rule('usd','USD inputs retain their values.', 'eq',pluck('output.docs','amount_usd'),pluck('input.docs','amount'),when={'op':'eq','args':[p('input.unit'),lit('USD')]}),
        rule('source','Normalization retains source IDs.', 'eq',pluck('output.docs','id'),pluck('input.docs','id'))])
    action('aggregate','Result','Evidence',['calls','charged'],[
        rule('count','Return the requested number of samples.', 'eq',{'count':p('output.samples')},p('input.sample_count')),
        rule('independence','Fresh-sampling mode requires distinct invocation IDs; this alone does not prove statistical independence.', 'unique',pluck('output.samples','call_id'),when={'op':'eq','args':[p('input.sampling_requirement'),lit('fresh')]}),
        rule('lineage','Aggregation retains source IDs required by the delivery contract.', 'eq',p('output.source_ids'),pluck('input.docs','id'))])
    action('conclude','Evidence','Decision',[],[
        rule('uncertainty','Failed retrieval cannot assert that records are absent.', 'eq',p('output.decision'),lit('unknown'),when={'op':'eq','args':[p('input.response_status'),lit('failed')]}),
        rule('lineage','Conclusion retains source IDs.', 'eq',p('output.source_ids'),p('input.source_ids'))])
    edges = [{'from':a, 'to':b,'bindings':[{'output':k,'input':k} for k in fields]} for a,b,fields in [
        ('retrieve','normalize',['query','tenant','status','docs']),
        ('normalize','aggregate',['query','tenant','status','docs']),
        ('aggregate','conclude',['query','tenant','status','docs','samples','source_ids'])]]
    return {'schema_version':1, 'system':'retrieval-demo', 'types':{'Query':base,'Result':result,'Evidence':evidence,'Decision':decision},
            'actions':actions,'edges':edges,'required_actions':[a['id'] for a in actions],
            'probes':{'base':{'tenant':'acme','amount':125,'sample_count':3,'unit':'cents','warm_cache':False,'retry':False,'response_status':'found','sampling_requirement':'fresh'},
                      'axes':{'response_status':['found','empty','failed'],'unit':['USD','cents'],'warm_cache':[False,True],'retry':[False,True],'sample_count':[1,3]}}}


if __name__ == '__main__':
    (CANDIDATE/'assets/retrieval.model.json').write_text(json.dumps(model(),ensure_ascii=False,indent=2)+'\n')
