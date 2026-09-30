import json
from pathlib import Path
import tempfile
import unittest
from .protocol import audit, valid
from .defaults import DEFAULT


class ProtocolTests(unittest.TestCase):
    def test_replay_is_audited_but_arbitrary_python_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'task.json';p.write_text('{}')
            allowed={'task.json','replay.py','composition.py','model.json','system.py'}
            self.assertTrue(audit('cat task.json',d,allowed)[0])
            self.assertTrue(audit("python3 -B replay.py '"+json.dumps(DEFAULT)+"'",d,allowed)[2])
            self.assertFalse(audit('python3 -c "print(1)"',d,allowed)[0])
            self.assertFalse(audit('cat ../secret.json',d,allowed)[0])
            self.assertFalse(audit('python3 replay.py > x',d,allowed)[0])

    def test_output_requires_all_fields_and_valid_configuration(self):
        p={'configuration':DEFAULT,'initial_verdict':'satisfied','explanation':'','verified':False}
        self.assertTrue(valid(p));self.assertFalse(valid({**p,'verified':'true'}))
        self.assertFalse(valid({**p,'configuration':{}}))
