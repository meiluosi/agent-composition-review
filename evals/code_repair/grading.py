"""Post-run artifact checks and isolated-process behavioral acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
HELPER=ROOT/'candidates/v4/agent-composition-review/scripts/composition.py'
spec=importlib.util.spec_from_file_location('composition_code_eval',HELPER)
composition=importlib.util.module_from_spec(spec);spec.loader.exec_module(composition)


def structure_check(model,source):
    errors=[]
    try:
        composition.validate(model)
        scope=model['scope']
        if not isinstance(scope['description'],str) or not scope['description'].strip():errors.append('scope description missing')
        if not isinstance(scope['limitations'],list) or not all(isinstance(x,str) for x in scope['limitations']):errors.append('scope limitations invalid')
        evidence=scope['evidence'];lines=source.splitlines()
        if not isinstance(evidence,list) or not evidence:errors.append('no code evidence')
        else:
            for e in evidence:
                if e['path']!='app.py' or type(e['start']) is not int or type(e['end']) is not int or not 1<=e['start']<=e['end']<=len(lines):
                    errors.append('invalid code location')
                if not isinstance(e['supports'],str) or not e['supports'].strip():errors.append('empty evidence description')
        if not all(isinstance(a.get('meaning'),str) and a['meaning'].strip() for a in model['actions']):errors.append('action meaning missing')
    except (KeyError,TypeError,ValueError,AttributeError) as e:errors.append(str(e))
    return {'valid':not errors,'errors':errors,'scope':'Syntax/references only; semantic criteria reviewed independently.'}


def behavioral_check(family,source,timeout=30):
    with tempfile.TemporaryDirectory(prefix='composition-grade-') as directory:
        stage=Path(directory);(stage/'app.py').write_text(source)
        (stage/'acceptance.py').write_bytes((ROOT/'evals/code_repair/acceptance.py').read_bytes())
        command=[sys.executable,'-B',str(stage/'acceptance.py'),family,str(stage/'app.py')]
        sandbox='process_timeout_only'
        if platform.system()=='Darwin':
            policy='(version 1) (allow default) (deny network*) (deny file-write*) (allow file-write* (subpath '+json.dumps(str(stage.resolve()))+'))'
            (stage/'policy.sb').write_text(policy)
            command=['/usr/bin/sandbox-exec','-f',str(stage/'policy.sb'),*command];sandbox='macos_no_network_writes_only_grade_directory'
        env=os.environ.copy();env.update(TMPDIR=directory,PYTHONDONTWRITEBYTECODE='1',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
        # No credentials are needed by the app or grader.
        for name in list(env):
            if any(s in name.upper() for s in ('TOKEN','API_KEY','SECRET','PASSWORD')):env.pop(name)
        proc=subprocess.Popen(command,cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True,text=True)
        timed_out=False
        try:stdout,stderr=proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate()
        try:result=json.loads(stdout)
        except ValueError:result={'tests_run':0,'passed':False,'checks':[],'invalid_output':True}
        if not isinstance(result,dict):result={'tests_run':0,'passed':False,'checks':[],'invalid_output':True}
        expected={'graph':3,'transaction':7,'async_group':5}[family]
        result['passed']=bool(result.get('passed') is True and proc.returncode==0 and not timed_out
                              and result.get('tests_run')==expected and len(result.get('checks',[]))==expected
                              and all(c.get('passed') is True for c in result['checks']))
        result.update(exit_code=proc.returncode,timed_out=timed_out,sandbox=sandbox,stderr=stderr.replace(directory,'<grade>'))
        for c in result.get('checks',[]):
            if 'error' in c:c['error']=c['error'].replace(directory,'<grade>')
        return result
