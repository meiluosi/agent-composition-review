"""Post-hoc busy-commit failure followed by reuse of the caller connection."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile


def check(path):
    spec=importlib.util.spec_from_file_location('commit_candidate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        database=str(Path(directory)/'test.db')
        writer=sqlite3.connect(database,timeout=0);reader=sqlite3.connect(database,timeout=0)
        try:
            writer.execute('PRAGMA journal_mode=DELETE')
            writer.execute('CREATE TABLE records(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
            reader.execute('BEGIN');reader.execute('SELECT * FROM records').fetchall()
            try:module.write_batch(writer,[('batch',1)]);outcome='normal_return'
            except Exception as error:outcome=type(error).__name__
            remaining=writer.execute('SELECT * FROM records ORDER BY key').fetchall()
            active=writer.in_transaction
            reader.rollback()
            count=None;visible=None;reuse_error=None
            try:
                count=module.write_batch(writer,[('next',2)])
                visible=reader.execute('SELECT * FROM records ORDER BY key').fetchall()
            except Exception as error:reuse_error=type(error).__name__
            passed=outcome=='OperationalError' and remaining==[] and count==1 and visible==[('next',2)]
            return {'passed':passed,'outcome':outcome,'failed_batch_remaining':remaining,
                    'transaction_active_after_failure':active,'next_batch_count':count,'next_batch_visible':visible,'reuse_error':reuse_error}
        finally:
            reader.close();writer.close()


if __name__=='__main__':print(json.dumps(check(Path(sys.argv[1])),indent=2))
