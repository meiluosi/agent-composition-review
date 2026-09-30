"""Check that acceptance discriminates real runtime faults and inadequate repairs."""
from pathlib import Path
import tempfile
import unittest
from .acceptance import evaluate
from .catalog import TASKS, cases


class FixtureTests(unittest.TestCase):
    def test_reference_versions_pass_and_originals_fail(self):
        for family in ('graph','transaction','async_group'):
            with self.subTest(family=family):
                broken=evaluate(family,TASKS/family/'app.py')
                healthy=evaluate(family,TASKS/family/'healthy.py')
                self.assertFalse(broken['passed']);self.assertTrue(healthy['passed'],healthy)
                self.assertEqual(broken['tests_run'],healthy['tests_run'])

    def test_behavior_changing_shortcuts_are_rejected(self):
        mutations={
            'graph':(TASKS/'graph/healthy.py').read_text().replace("parent.add_edge('prepare','analysis')","parent.add_edge('refresh','analysis')"),
            'transaction':(TASKS/'transaction/healthy.py').read_text().replace('    return len(rows)','    connection.commit()\n    return len(rows)'),
            'async_group':(TASKS/'async_group/healthy.py').read_text().replace('return await asyncio.gather(*tasks)','return await asyncio.gather(*tasks,return_exceptions=True)')}
        for family,source in mutations.items():
            with self.subTest(family=family),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'app.py';path.write_text(source)
                self.assertFalse(evaluate(family,path)['passed'])

    def test_catalog_has_three_real_runtime_families_with_normal_pairs(self):
        records=cases();self.assertEqual(len(records),6)
        for family in ('graph','transaction','async_group'):
            self.assertEqual({c['variant'] for c in records if c['family']==family},{'incident','normal'})
            self.assertTrue(all((TASKS.parents[2]/c['source_file']).is_file() for c in records if c['family']==family))


if __name__=='__main__':unittest.main()
