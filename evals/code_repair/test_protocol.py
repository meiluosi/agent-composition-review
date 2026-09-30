"""Check isolation, bad submissions, and separation of execution from model grading."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from .catalog import cases,TASKS
from .grading import ROOT,behavioral_check,structure_check
from .protocol import review_input,review_valid
sys.path.insert(0,str(ROOT/'scripts'))
import run_code_repair_eval as runner
from analyze_code_repair import summarize


class ProtocolTests(unittest.TestCase):
    def test_basic_workspace_contains_no_oracle_or_model(self):
        case=cases()[0];case['source']=(ROOT/case['source_file']).read_text()
        with tempfile.TemporaryDirectory() as d:
            stage=Path(d);runner.stage_task(stage,case,'A_basic')
            self.assertEqual({p.name for p in stage.iterdir()},{'app.py','original.py','task.md','smoke.py','MODEL_FORMAT.md','run_python'})
            self.assertEqual((stage/'app.py').read_text(),case['source'])
            self.assertNotIn('modeling_rubric',(stage/'task.md').read_text())

    def test_code_anchors_do_not_imply_behavior_success(self):
        model=json.loads((runner.SOURCE/'assets/retrieval.model.json').read_text())
        model['scope']={'description':'Demonstration only','limitations':['No semantic verification'],
                        'evidence':[{'path':'app.py','start':1,'end':1,'supports':'Function exists'}]}
        for action in model['actions']:action['meaning']='Example action'
        self.assertTrue(structure_check(model,'def f(): pass')['valid'])
        model['scope']['evidence'][0]['end']=2
        self.assertFalse(structure_check(model,'def f(): pass')['valid'])
        self.assertFalse(behavioral_check('transaction','raise RuntimeError("invalid submission")')['passed'])
        result=behavioral_check('transaction','while True: pass',timeout=.15)
        self.assertTrue(result['timed_out']);self.assertFalse(result['passed'])

    def test_blind_packet_omits_condition_and_tool_logs(self):
        case=cases()[0];case.update(condition='C_v4',tool_logs='SECRET',trial=2)
        packet=review_input(case,'a','b',{}, {}, {})
        self.assertFalse({'condition','trial','tool_logs','provenance'} & set(packet))
        self.assertNotIn('SECRET',json.dumps(packet))
        valid={'criteria':[{'id':f'r{i}','assessment':'covered','model_evidence':'x','code_evidence':'y','reason':'z'} for i in (1,2,3)],'uncertainty':''}
        self.assertTrue(review_valid(valid));valid['criteria'][2]['id']='r1';self.assertFalse(review_valid(valid))

    def test_failed_status_cannot_count_as_behavior_success(self):
        cs={c['case_id']:c for c in cases()};rows=[]
        for arm in runner.ARMS:
            for c in cs:
                for trial in (1,2):
                    rows.append({'condition':arm,'case_id':c,'trial':trial,'status':'workspace_violation',
                      'acceptance':{'passed':True},'prediction':{},'source_changed':False,'structure':{'valid':True},
                      'review':{'valid':False},'tool_audit':[],'elapsed_seconds':1})
        result=summarize(rows,cs)
        for arm in runner.ARMS:
            self.assertEqual(result['conditions'][arm]['n'],12)
            self.assertEqual(result['conditions'][arm]['behavior_pass'],0)


if __name__=='__main__':unittest.main()
