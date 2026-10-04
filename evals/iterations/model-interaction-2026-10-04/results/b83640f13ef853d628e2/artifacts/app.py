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
        # Filter insertion order is irrelevant, while tenant, query and
        # revision together identify a source result.
        key = (tenant, json.dumps(query, sort_keys=True, ensure_ascii=False), revision)
        entry = self._entries.get(key)
        if entry is not None and now < entry[0]:
            documents = entry[1]
        else:
            # The source may retain and mutate its arguments or result. Keep
            # both sides isolated from the caller and from cached state.
            source_query = deepcopy(query)
            source_documents = self._source(tenant, source_query, revision)
            documents = deepcopy(source_documents)
            self._entries[key] = (now + self._ttl, documents)
        return rank_documents(documents, limit)
