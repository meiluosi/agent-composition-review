# Queue review

Initial verdict: **violation**.

## Findings and repairs

- `ack` checked job ID and worker but omitted the receipt's generation. After
  expiry and reclaim by the same worker, an old receipt could complete the new
  claim. The atomic UPDATE now also matches generation. Tests cover immediate
  reuse, worker-name cycles, and concurrent reclaim at the expiry boundary.
- `claim` accepted `True` as a positive lease duration. Booleans are now rejected
  with `ValueError` before opening a transaction.
- Nonempty Python string identifiers containing lone Unicode surrogates failed
  SQLite's UTF-8 TEXT conversion. These strings now use UTF-8 surrogatepass bytes
  as SQLite BLOB parameters. Ordinary strings retain their existing TEXT storage;
  the schema and existing data remain compatible. Receipts remain opaque
  dictionaries, and worker binding uses the same conversion during claim and ack.

Preserved: JSON serialization before insertion, payload snapshots, FIFO ordering,
fractional Python expiry arithmetic, exact-boundary expiry, permanent ID uniqueness,
transaction rollback, and BEGIN IMMEDIATE serialization of claims. No public
method signatures changed. app.py imports only standard-library modules and does
not depend on the added tests or this report.

## Executed verification

- `./run_python smoke.py original.py`: passed; the public smoke does not expose
  the identified violations.
- `./run_python test_queue.py original.py`: final suite ran 13 tests and reported
  5 assertion failures and 2 Unicode encoding errors, exposing the findings above.
- `./run_python test_queue.py app.py`: all 13 tests passed, including 600 seeded
  reference-model operations, invalid payload atomicity, persistence, legacy
  database/receipt compatibility, payload isolation, and numeric boundaries.
- `TMPDIR="$PWD" ./run_python smoke.py app.py`: passed.

An earlier version of the depth fixture assumed `sys.getrecursionlimit() + 10`
exceeded the JSON encoder's limit. A direct Python 3.12.14 probe showed that depth
was accepted, so it was not an implementation violation. The final fixture uses
10,000 levels and first verifies that the standard encoder itself raises
RecursionError, then checks queue rejection without mutation.

Concurrency tests use eight threads with independent SQLite connections for
claim, ack, and expiry reclaim. No multi-process stress test or exhaustive
interleaving exploration was performed. The tests provide finite regression
evidence, not a proof for every possible execution.
