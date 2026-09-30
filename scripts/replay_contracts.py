#!/usr/bin/env python3
"""Run generated inputs through a real local adapter and record contract checks; no LLM calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.replay.runtime import composition as c
from evals.replay.make_model import model
from evals.replay.system import OPTIONS, run
from evals.replay.defaults import DEFAULT


def run_suite():
    m=model(); scenarios=c.generate(m)['cases']; records=[]
    configurations={'healthy':DEFAULT}
    configurations.update({key:{**DEFAULT,key:next(v for v in vals if v!=DEFAULT[key])} for key,vals in OPTIONS.items()})
    for name,config in configurations.items():
        checks=[c.check(m,run(config,scenario)) for scenario in scenarios]
        records.append({'configuration':name,'inputs':len(scenarios),
                        'satisfied':sum(x['verdict']=='satisfied' for x in checks),
                        'violation':sum(x['verdict']=='violation' for x in checks),
                        'insufficient':sum(x['verdict']=='insufficient' for x in checks),
                        'checks':checks})
    result={'kind':'deterministic_adapter_mutation_regression_not_model_evaluation',
            'model_sha256':c.digest(m),'plan':c.plan(m),'probe_generation':c.generate(m),
            'records':records,'limitations':['Authored synthetic local system and fake services, not production traces.',
                                            'Contracts and mutants authored together; not independent validation of skill capability.']}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'evals/runs/private/replay-contracts.json')
    args=parser.parse_args(); result=run_suite()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in r.items() if k!='checks'} for r in result['records']],indent=2))


if __name__=='__main__':main()
