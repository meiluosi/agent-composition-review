"""Cache ranked retrieval results for caller requests."""
from copy import deepcopy
import json


def rank_documents(documents, limit):
    ordered = sorted(documents, key=lambda document: (-document['score'], document['id']))
    return deepcopy(ordered[:limit])


class RetrievalCache:
    def __init__(self, source, ttl=10):
        self._source = source
        self._ttl = ttl
        self._entries = {}

    def search(self, tenant, query, revision, now, limit=10):
        if limit == 0:
            return []
        key = (tenant, json.dumps(query, sort_keys=True, ensure_ascii=False), revision)
        entry = self._entries.get(key)
        if entry is not None and now < entry[0]:
            documents = entry[1]
        else:
            # Isolate both sides of the source boundary from retained mutations.
            documents = deepcopy(self._source(tenant, deepcopy(query), revision))
            self._entries[key] = (now + self._ttl, documents)
        return rank_documents(documents, limit)
