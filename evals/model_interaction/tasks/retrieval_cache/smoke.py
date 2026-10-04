"""A basic public example, not exhaustive acceptance."""
from app import RetrievalCache, rank_documents

calls = []
documents = [{'id': 'only', 'score': 4, 'payload': {'text': 'result', 'tags': ['demo']}}]
def source(tenant, query, revision):
    calls.append((tenant, query, revision))
    return documents

cache = RetrievalCache(source, ttl=10)
query = {'text': 'example', 'filters': {}}
assert cache.search('team', query, 'v1', now=0, limit=5) == documents
assert cache.search('team', query, 'v1', now=1, limit=5) == documents
assert len(calls) == 1
assert rank_documents(documents, 5) == documents
print('basic cache example passed')
