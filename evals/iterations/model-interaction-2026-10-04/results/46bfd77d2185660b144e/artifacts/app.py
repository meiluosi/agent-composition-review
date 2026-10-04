"""Cache ranked retrieval results for caller requests."""
from copy import deepcopy


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
        # Filters are a mapping, so their insertion order is not part of the
        # query identity. The remaining request fields are all significant.
        key = (tenant, revision, query['text'], tuple(sorted(query['filters'].items())))
        entry = self._entries.get(key)
        if entry is not None and now < entry[0]:
            documents = entry[1]
        else:
            # Keep source code from changing caller-owned query data, and keep
            # its retained references separate from the cached snapshot.
            source_documents = self._source(tenant, deepcopy(query), revision)
            documents = deepcopy(source_documents)
            self._entries[key] = (now + self._ttl, documents)
        return rank_documents(documents, limit)
