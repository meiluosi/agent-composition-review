#!/usr/bin/env python3
"""Read-only local replay: python3 replay.py [JSON_CONFIGURATION]."""
import json
from pathlib import Path
import sys
from system import run
from composition import generate, check

if __name__=='__main__':
    task=json.loads(Path('task.json').read_text())
    config=json.loads(sys.argv[1]) if len(sys.argv)>1 else task['initial']
    m=json.loads(Path('model.json').read_text()); cases=generate(m)['cases']
    failures=[]
    for i,case in enumerate(cases):
        record=check(m,run(config,case))
        if record['verdict']!='satisfied':
            failures.append({'index':i,'input':case,'verdict':record['verdict'],
                             'checks':[x for x in record['checks'] if x['status'] in ('fail','unknown')]})
    print(json.dumps({'inputs':len(cases),'passed':len(cases)-len(failures),'failure_count':len(failures),'examples':failures[:3], 'failed_obligations':sorted({c['obligation_id'] for r in failures for c in r['checks']})},ensure_ascii=False))
