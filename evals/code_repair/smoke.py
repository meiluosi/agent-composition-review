"""Create the public single-input smoke for each family (not hidden acceptance)."""
SMOKES={
'graph':'''from app import build_graph
print(build_graph().invoke({'count':1,'label':'old','left':2,'right':3}))
''',
 'transaction':'''import sqlite3
from app import write_batch
connection=sqlite3.connect(':memory:')
connection.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
print(write_batch(connection,[('example',5)]))
print(connection.execute('SELECT * FROM records').fetchall())
connection.close()
''',
 'async_group':'''import asyncio
from app import run_group
async def work(n):
    await asyncio.sleep(0)
    return n*2
async def main():
    print(await run_group([lambda:work(1),lambda:work(2)]))
asyncio.run(main())
'''}
