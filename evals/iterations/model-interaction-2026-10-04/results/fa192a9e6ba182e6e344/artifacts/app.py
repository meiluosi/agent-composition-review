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
        # Filter insertion order is irrelevant, but every request dimension is
        # part of the identity of a source result.
        key = (tenant, query['text'], tuple(sorted(query['filters'].items())), revision)
        entry = self._entries.get(key)
        if entry is not None and now < entry[0]:
            documents = entry[1]
        else:
            # The source is allowed to retain and mutate its arguments and
            # results. Keep both its query and its result isolated from us.
            source_query = deepcopy(query)
            source_documents = self._source(tenant, source_query, revision)
            documents = deepcopy(source_documents)
            self._entries[key] = (now + self._ttl, documents)
        return rank_documents(documents, limit)
