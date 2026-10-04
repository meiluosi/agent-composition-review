# Repair the retrieval-result cache

`app.py` exposes `rank_documents(documents, limit)` and `RetrievalCache(source, ttl=10)`. Correct contract violations while preserving these APIs. A conforming implementation may be left unchanged. Use only Python 3.12's standard library.

A document is an object with exactly `id` (a nonempty string), `score` (an integer, excluding booleans), and `payload` (a finite JSON object with at most 20 nested object/array containers on any path, counting the payload object itself). Document IDs are unique within a source result. Scores may be zero, negative or large. `rank_documents` returns up to `limit` documents in descending score order, breaking ties by ascending Python string order of `id`. It must not change the input and returns independent copies, including nested payloads. `limit` is a nonnegative integer; zero returns an empty list.

`source(tenant, query, revision)` returns a list of these documents, possibly empty, or raises an exception. `tenant` and `revision` are nonempty strings. `query` has exactly `text` (a string) and `filters` (a dictionary from strings to strings). Filter insertion order does not change query identity. Query text, every filter key/value, tenant and revision all affect which result is requested.

`cache.search(tenant, query, revision, now, limit=10)` returns ranked documents under the following rules:

- The cache's contents are private to that instance. A successful source result can be reused only for the same tenant, query and revision. Ranking limits do not affect result identity; caching a small first response must not lose other documents on a later, larger-limit request.
- `ttl` is a positive integer. `now` is a nonnegative integer that never decreases across calls to the same instance. A successful source result acquired at time `t` is reusable while `now < t + ttl`; at equality it has expired. A hit does not extend its lifetime. Expired results must not be returned.
- A cache miss or expiration invokes the source once and retains the full successful result. Source exceptions propagate unchanged, are not cached, and must not erase still-valid results for other identities. The instance stays usable; a later call may retry. An empty successful result is still cacheable.
- A zero-limit search returns `[]` without invoking the source or populating/refreshing an entry.
- The cache does not mutate caller queries or document lists. The source may mutate or retain its query argument, and may retain and later mutate its returned documents. These changes must not affect the caller's query or cached results. Mutating results returned by either API must not affect future results or source-owned documents. Nested dictionaries and lists are included in these guarantees.

Calls are sequential. Caller arguments and successful source results have the documented types and shapes; argument-validation policy for other values is out of scope. No capacity eviction, background refresh, threads or external storage is required. Keep the implementation self-contained and expose no new required arguments or methods.
