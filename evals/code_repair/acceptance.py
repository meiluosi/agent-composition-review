"""Independent behavioral checks; copy only to a post-run grading workspace."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

APP=None


class GraphAcceptance(unittest.TestCase):
    def test_outputs_and_reuse(self):
        from langgraph.graph.state import CompiledStateGraph
        graph=APP.build_graph()
        self.assertIsInstance(graph,CompiledStateGraph)
        self.assertTrue(list(graph.get_subgraphs()))
        for count,label,left,right in [(0,'',0,0),(-9,'hello',3,8),(100,'汉字',-4,9),(0,'',0,0)]:
            got=graph.invoke(dict(count=count,label=label,left=left,right=right))
            self.assertEqual(got,dict(count=count+7,label=label+'|fresh',left=left+count+7+len(label),right=right+count+7-len(label)))

    def test_both_concurrency_boundaries_and_single_execution(self):
        calls=[]
        g=APP.build_graph(threading.Barrier(2,timeout=2),threading.Barrier(2,timeout=2),calls)
        result=g.invoke(dict(count=1,label='a',left=2,right=3))
        self.assertEqual(result,dict(count=8,label='a|fresh',left=11,right=10))
        self.assertCountEqual(calls,['prepare','refresh','left','right'])

    def test_separate_graphs_do_not_share_state(self):
        a=APP.build_graph();b=APP.build_graph()
        one={'count':2,'label':'long','left':10,'right':0}
        two={'count':4,'label':'b','left':-1,'right':2}
        self.assertEqual(a.invoke(one),a.invoke(one))
        self.assertEqual(b.invoke(two)['left'],11)
        self.assertEqual(one,{'count':2,'label':'long','left':10,'right':0})


class TransactionAcceptance(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.path=Path(self.directory.name)/'db.sqlite'
        self.conn=sqlite3.connect(self.path)
        self.conn.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)');self.conn.commit()
    def tearDown(self):
        self.conn.close();self.directory.cleanup()
    def rows(self,conn=None):
        return (conn or self.conn).execute('SELECT key,value FROM records ORDER BY key').fetchall()
    def external(self):
        with sqlite3.connect(self.path) as reader:return self.rows(reader)
    def test_standalone_success_is_committed(self):
        self.assertEqual(APP.write_batch(self.conn,[('a',2),('b',3)]),2)
        self.assertEqual(self.external(),[('a',2),('b',3)])
        self.assertFalse(self.conn.in_transaction)
    def test_standalone_failure_is_atomic_and_preserves_original_error(self):
        with self.assertRaises(sqlite3.IntegrityError):APP.write_batch(self.conn,[('a',1),('a',2)])
        self.assertEqual(self.rows(),[]);self.assertEqual(self.external(),[])
        self.assertEqual(APP.write_batch(self.conn,[('next',4)]),1)
    def test_success_does_not_commit_callers_transaction(self):
        self.conn.execute('INSERT INTO records VALUES (?,?)',('outer',10))
        self.assertEqual(APP.write_batch(self.conn,[('inner',20)]),1)
        self.assertTrue(self.conn.in_transaction);self.assertEqual(self.external(),[])
        self.assertEqual(self.rows(),[('inner',20),('outer',10)])
        self.conn.rollback();self.assertEqual(self.rows(),[])
    def test_failure_rolls_back_only_own_batch(self):
        self.conn.execute('INSERT INTO records VALUES (?,?)',('outer',10))
        with self.assertRaises(sqlite3.IntegrityError):APP.write_batch(self.conn,[('inner',20),('outer',30)])
        self.assertTrue(self.conn.in_transaction);self.assertEqual(self.rows(),[('outer',10)])
        self.conn.commit();self.assertEqual(self.external(),[('outer',10)])
    def test_explicit_transaction_in_autocommit_mode(self):
        self.conn.isolation_level=None
        self.conn.execute('BEGIN');self.conn.execute("INSERT INTO records VALUES ('outer',1)")
        with self.assertRaises(sqlite3.IntegrityError):APP.write_batch(self.conn,[('new',2),('outer',3)])
        self.assertTrue(self.conn.in_transaction);self.assertEqual(self.rows(),[('outer',1)])
        APP.write_batch(self.conn,[('later',4)]);self.assertEqual(self.external(),[])
        self.conn.rollback();self.assertEqual(self.rows(),[])
    def test_existing_savepoint_is_preserved(self):
        self.conn.execute('BEGIN');self.conn.execute('SAVEPOINT caller')
        APP.write_batch(self.conn,[('inner',5)])
        self.conn.execute('ROLLBACK TO caller');self.conn.execute('RELEASE caller')
        self.assertEqual(self.rows(),[]);self.assertTrue(self.conn.in_transaction)
    def test_empty_batch_and_repeat_failure_do_not_end_transaction(self):
        self.conn.execute('INSERT INTO records VALUES (?,?)',('outer',10))
        self.assertEqual(APP.write_batch(self.conn,[]),0)
        for _ in range(2):
            with self.assertRaises(sqlite3.IntegrityError):APP.write_batch(self.conn,[('outer',1)])
        self.assertTrue(self.conn.in_transaction);self.assertEqual(self.rows(),[('outer',10)])
        APP.write_batch(self.conn,[("quote'key",-8)])
        self.conn.rollback();self.assertEqual(self.rows(),[])


class AsyncAcceptance(unittest.IsolatedAsyncioTestCase):
    async def bounded(self,coro):return await asyncio.wait_for(coro,timeout=2)
    async def test_order_empty_and_concurrency(self):
        started=0;gate=asyncio.Event()
        async def worker(index):
            nonlocal started
            started+=1
            if started==3:gate.set()
            await gate.wait();return index*2
        self.assertEqual(await self.bounded(APP.run_group([lambda i=i:worker(i) for i in range(3)])),[0,2,4])
        self.assertEqual(await APP.run_group([]),[])
    async def test_failure_waits_for_sibling_cleanup_and_keeps_exception(self):
        entered=asyncio.Event();cleaned=asyncio.Event();sentinel=ValueError('tool-failed')
        async def slow():
            entered.set()
            try:await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0);cleaned.set()
        async def fail():
            await entered.wait();raise sentinel
        with self.assertRaises(ValueError) as raised:await self.bounded(APP.run_group([slow,fail]))
        self.assertIs(raised.exception,sentinel);self.assertTrue(cleaned.is_set())
    async def test_child_cancellation_cleans_other_children(self):
        entered=asyncio.Event();cleaned=asyncio.Event()
        async def slow():
            entered.set()
            try:await asyncio.Event().wait()
            finally:cleaned.set()
        async def cancel():
            await entered.wait();raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.bounded(APP.run_group([slow,cancel]))
        self.assertTrue(cleaned.is_set())
    async def test_factory_failure_cleans_already_started_children(self):
        # The first coroutine may be scheduled or not: it must not remain pending after return.
        created=[]
        async def worker():await asyncio.Event().wait()
        def first():
            coro=worker();created.append(coro);return coro
        def fail():raise LookupError('factory-failed')
        try:
            with self.assertRaises(LookupError):await self.bounded(APP.run_group([first,fail]))
            for coro in created:self.assertIsNone(coro.cr_frame)
        finally:
            for coro in created:coro.close()
    async def test_external_cancellation_and_unrelated_task_survives(self):
        started=asyncio.Event();cleaned=asyncio.Event();unrelated=asyncio.create_task(asyncio.Event().wait())
        async def worker():
            started.set()
            try:await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0);cleaned.set()
        group=asyncio.create_task(APP.run_group([worker]))
        try:
            await self.bounded(started.wait());group.cancel()
            with self.assertRaises(asyncio.CancelledError):await self.bounded(group)
            self.assertTrue(cleaned.is_set());self.assertFalse(unrelated.done())
        finally:
            unrelated.cancel();await asyncio.gather(unrelated,return_exceptions=True)


SUITES={'graph':GraphAcceptance,'transaction':TransactionAcceptance,'async_group':AsyncAcceptance}


def evaluate(family,path):
    global APP
    spec=importlib.util.spec_from_file_location('submission',path);APP=importlib.util.module_from_spec(spec);spec.loader.exec_module(APP)
    class Result(unittest.TestResult):
        def __init__(self):super().__init__();self.rows=[]
        def addSuccess(self,test):super().addSuccess(test);self.rows.append({'test':test._testMethodName,'passed':True})
        def addFailure(self,test,err):super().addFailure(test,err);self.rows.append({'test':test._testMethodName,'passed':False,'error':self._exc_info_to_string(err,test)})
        def addError(self,test,err):super().addError(test,err);self.rows.append({'test':test._testMethodName,'passed':False,'error':self._exc_info_to_string(err,test)})
    result=Result();unittest.defaultTestLoader.loadTestsFromTestCase(SUITES[family]).run(result)
    return {'tests_run':result.testsRun,'passed':result.wasSuccessful() and not result.skipped and result.testsRun==len(result.rows),'checks':result.rows}


if __name__=='__main__':
    result=evaluate(sys.argv[1],Path(sys.argv[2]));print(json.dumps(result,ensure_ascii=False,indent=2))
    sys.exit(0 if result['passed'] else 1)
