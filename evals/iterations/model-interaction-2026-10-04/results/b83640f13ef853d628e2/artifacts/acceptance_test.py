from app import RetrievalCache, rank_documents


def doc(identity, score, value):
    return {"id": identity, "score": score, "payload": {"nested": [value]}}


# Ranking: score descending, string ID ascending for ties, independent deep copies.
items = [doc("z", 8, 1), doc("a", 8, 2), doc("low", -10, 3)]
ranked = rank_documents(items, 2)
assert [item["id"] for item in ranked] == ["a", "z"]
ranked[0]["payload"]["nested"][0] = "changed"
assert items[1]["payload"]["nested"][0] == 2
assert rank_documents(items, 0) == []


# Cache isolates caller query, source query, source-owned results, and returned results.
calls = []
source_queries = []
source_results = []


def source(tenant, query, revision):
    calls.append((tenant, revision))
    source_queries.append(query)
    query["filters"]["source_mutation"] = "yes"
    result = [doc("one", 1, [tenant, revision])]
    source_results.append(result)
    return result


cache = RetrievalCache(source, ttl=4)
query = {"text": "q", "filters": {"b": "2", "a": "1"}}
original_query = {"text": "q", "filters": {"b": "2", "a": "1"}}
first = cache.search("t1", query, "r1", 5, limit=1)
assert query == original_query
first[0]["payload"]["nested"][0] = "returned mutation"
source_results[0][0]["payload"]["nested"][0] = "source mutation"
source_queries[0]["text"] = "source retained mutation"
reordered_filters = {"text": "q", "filters": {"a": "1", "b": "2"}}
second = cache.search("t1", reordered_filters, "r1", 8, limit=10)
assert len(calls) == 1  # Hit at t+ttl-1; filter insertion order is irrelevant.
assert second[0]["payload"]["nested"][0] == ["t1", "r1"]
second[0]["payload"]["nested"][0].append("caller mutation")
assert cache.search("t1", query, "r1", 8, limit=10)[0]["payload"]["nested"][0] == ["t1", "r1"]

# Tenant and revision both participate in identity; the cached full list serves larger limits.
for tenant, revision in (("t2", "r1"), ("t1", "r2")):
    cache.search(tenant, query, revision, 8)
assert len(calls) == 3

# Expiration is exclusive at t+ttl; a hit does not extend the lifetime.
cache.search("t1", query, "r1", 9)
assert len(calls) == 4

# Zero limit neither invokes the source nor refreshes/populates an entry.
count = len(calls)
assert cache.search("new", query, "r1", 10, limit=0) == []
assert len(calls) == count

# Empty success is cached. Exceptions are propagated unchanged and not cached;
# a failed refresh does not remove a still-valid different identity.
failure = RuntimeError("source failed")
tries = []
other_result = [doc("other", 2, "valid")]


def flaky(tenant, query, revision):
    tries.append((tenant, revision))
    if tenant == "broken":
        raise failure
    return [] if tenant == "empty" else other_result


flaky_cache = RetrievalCache(flaky, ttl=3)
assert flaky_cache.search("empty", {"text": "", "filters": {}}, "r", 0) == []
assert flaky_cache.search("empty", {"text": "", "filters": {}}, "r", 2) == []
assert len(tries) == 1
flaky_cache.search("good", {"text": "", "filters": {}}, "r", 0)
try:
    flaky_cache.search("broken", {"text": "", "filters": {}}, "r", 1)
except RuntimeError as exc:
    assert exc is failure
else:
    raise AssertionError("source exception did not propagate")
assert flaky_cache.search("good", {"text": "", "filters": {}}, "r", 2)[0]["id"] == "other"
try:
    flaky_cache.search("broken", {"text": "", "filters": {}}, "r", 2)
except RuntimeError as exc:
    assert exc is failure
else:
    raise AssertionError("failed source result was cached")
assert len(tries) == 4

print("acceptance checks passed")
