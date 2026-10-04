from app import RetrievalCache, rank_documents

# Ranking, tie order, zero limit, and nested copy isolation.
docs = [
    {'id': 'b', 'score': 5, 'payload': {'items': [{'v': 1}]}},
    {'id': 'a', 'score': 5, 'payload': {'items': [{'v': 2}]}},
    {'id': 'low', 'score': -10, 'payload': {}},
]
ranked = rank_documents(docs, 2)
assert [d['id'] for d in ranked] == ['a', 'b']
assert rank_documents(docs, 0) == []
ranked[0]['payload']['items'][0]['v'] = 99
assert docs[1]['payload']['items'][0]['v'] == 2
assert [d['id'] for d in rank_documents(docs, 3)] == ['a', 'b', 'low']

# Identity includes every request field; filter insertion order is irrelevant.
calls = []
source_owned = [{'id': 'one', 'score': 1, 'payload': {'nested': [1]}}]
def source(tenant, query, revision):
    calls.append((tenant, query, revision))
    query['text'] = 'source changed its private copy'
    query['filters']['mutated'] = 'yes'
    return source_owned
cache = RetrievalCache(source, ttl=3)
q = {'text': 'q', 'filters': {'x': '1', 'y': '2'}}
assert cache.search('t', q, 'r', 0, 1) == source_owned
assert q == {'text': 'q', 'filters': {'x': '1', 'y': '2'}}
source_owned[0]['payload']['nested'][0] = 8
hit = cache.search('t', {'filters': {'y': '2', 'x': '1'}, 'text': 'q'}, 'r', 1, 4)
assert hit[0]['payload']['nested'] == [1]
hit[0]['payload']['nested'][0] = 7
assert cache.search('t', q, 'r', 2, 4)[0]['payload']['nested'] == [1]
assert len(calls) == 1
cache.search('other', q, 'r', 2)
cache.search('t', q, 'other-revision', 2)
cache.search('t', {'text': 'other', 'filters': {'x': '1', 'y': '2'}}, 'r', 2)
cache.search('t', {'text': 'q', 'filters': {'x': 'different', 'y': '2'}}, 'r', 2)
assert len(calls) == 5

# Strict expiration boundary and zero-limit no-op.
cache.search('t', q, 'r', 3, 0)
assert len(calls) == 5
cache.search('t', q, 'r', 3, 1)
assert len(calls) == 6

# Empty results are cacheable; exceptions propagate and are not cached or destructive.
attempts = 0
def flaky(tenant, query, revision):
    global attempts
    attempts += 1
    if attempts == 1:
        raise RuntimeError('source failure')
    return []
retry_cache = RetrievalCache(flaky, ttl=2)
try:
    retry_cache.search('t', {'text': 'x', 'filters': {}}, 'r', 0)
except RuntimeError as exc:
    assert str(exc) == 'source failure'
else:
    raise AssertionError('source exception did not propagate')
assert retry_cache.search('t', {'text': 'x', 'filters': {}}, 'r', 1) == []
assert retry_cache.search('t', {'text': 'x', 'filters': {}}, 'r', 2) == []
assert attempts == 2
print('acceptance checks passed')
